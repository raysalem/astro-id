"""``astro-id facet serve``: the astro session UI against a Facet library, standalone or as a Facet add-on.

Two modes share one ``App`` (the five-step session) and one static page:

- **standalone** (``facet serve``): binds 127.0.0.1 only; every URL carries a random per-start secret
  (``http://127.0.0.1:<port>/<secret>/``), any other path is 404, a request whose Host is not loopback is refused
  (DNS rebinding) and writes need a JSON body with no foreign Origin. Opens the page in a browser.
- **add-on** (``facet serve --addon``, started by Facet's supervisor, PLUGIN_BOUNDARY.md v0.2 §5/§8): reads
  ``FACET_URL``, ``FACET_TOKEN``, ``FACET_ADDON_ID``, ``FACET_LAUNCH_SECRET`` from the environment, binds
  127.0.0.1 port 0 (or ``FACET_PORT``) and prints exactly ``FACET_ADDON_PORT=<n>`` on stdout (everything else goes
  to stderr). ``GET /health?c=`` answers ``{"addon": id, "ack": hex HMAC-SHA256(secret, c)}``; every other request
  needs ``X-Facet-Launch: <secret>`` (403 otherwise). The page is served at ``/ui/`` and its API at ``/ui/api/...``
  with relative URLs only, because Facet's proxy mounts it at ``/addons/<id>/``. No cookies are set or read.

Flow (one page, five steps): photos (Facet's scope in add-on mode, else path prefix / album / tag) -> location
(GPS source per photo; set a missing one through ``PUT /api/photo/gps``) -> time (camera time, corrected time,
offset, tolerance, timezone, RAW/JPG; ``time_preview``) -> plate solve (background thread, JSON-lines events, then
the sync layer writes tags + annotations) -> culling (quality, keep/review/reject with reasons, editable rule,
re-cull without re-solving). Facet's own reject / cull flags are never written.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import webbrowser
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlparse

from .. import config as cfgmod
from ..cull import DEFAULT_RULE, merge_rule, validate_rule
from .client import ALL_FRAMES, FacetClient, FacetError, addon_credentials, client_from_env, connect
from .ledger import Ledger
from .sync import (TAG_REVIEW, SyncContext, apply_results, facet_hints, gps_source, parse_path_map, to_local)

log = logging.getLogger("astro_id.facet")
STATIC = Path(__file__).with_name("static")
LAST_SITE = "last_site"


def _elevation(lat: float, lon: float):
    """Ground elevation for a site picked without one (USGS EPQS; None when it cannot be looked up)."""
    from ..site import fetch_elevation

    try:
        return fetch_elevation(lat, lon, timeout=8)
    except Exception:
        return None
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
ADDON_ID = "astro-id"
BRIDGE_MARKER = "<!--ASTRO_BRIDGE-->"
MAX_SCOPE_PHOTOS = 2000
MAX_SELECTION = 500
MAX_QUERY = 2048
# Gallery keys the sidecar overrides: it wants every frame of every set, in its own order and page size.
SCOPE_DROP = {"page", "per_page", "sort", "dir", "nonce"} | set(ALL_FRAMES)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
PREFIX_RE = re.compile(r"^/addons/[a-z0-9][a-z0-9_-]{0,63}$")

STANDALONE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                  "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'")
# Mirrors the policy Facet's proxy sets (spec §8); the proxy drops ours and sets its own, this covers a direct load.
ADDON_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; "
             "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; base-uri 'none'; "
             "form-action 'none'")


def page_html() -> bytes:
    """The page with the bridge script inlined (no second script request, so it does not depend on whether
    ``script-src 'self'`` resolves inside an opaque-origin sandbox)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    bridge = (STATIC / "bridge.js").read_text(encoding="utf-8")
    return html.replace(BRIDGE_MARKER, "<script>\n" + bridge + "\n</script>", 1).encode("utf-8")


def health_ack(secret: str, challenge: str) -> str:
    return hmac.new(secret.encode("utf-8"), challenge.encode("utf-8"), sha256).hexdigest()


def safe_rel_path(raw_path: str) -> str | None:
    """The request path decoded once, or None when it must be refused: ``..``/``.`` segments, a backslash, NUL,
    an empty segment (``//``) anywhere but a trailing slash, or a path that does not start with ``/``."""
    try:
        p = unquote(raw_path, errors="strict")
    except UnicodeDecodeError:
        return None
    if not p.startswith("/") or "\\" in p or "\x00" in p:
        return None
    segs = p[1:].split("/")
    if any(s in ("..", ".") for s in segs) or any(s == "" for s in segs[:-1]):
        return None
    return p


class Job:
    """The one solve run of this server (a new solve replaces a finished one)."""

    def __init__(self):
        self.events: list[dict] = []
        self.running = False
        self.done: dict | None = None
        self.reports: list[dict] = []
        self.results: dict = {}           # facet path -> frame result
        self.error: str | None = None
        self.min_confidence = 65
        self.lock = threading.Lock()

    def emit(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)

    def snapshot(self, after: int) -> dict:
        with self.lock:
            return {"running": self.running, "events": self.events[after:], "next": len(self.events),
                    "done": self.done, "reports": self.reports, "error": self.error}


class App:
    def __init__(self, client: FacetClient, ledger: Ledger, ctx: SyncContext, path_map, addon_id: str | None = None):
        self.client, self.ledger, self.ctx, self.path_map = client, ledger, ctx, path_map
        self.addon_id = addon_id
        self.mount: str | None = None       # X-Forwarded-Prefix from Facet's proxy (informational)
        self.notes: list[str] = []          # start-up notes shown in the page (credential mode etc.)
        self.job = Job()
        self.photos: dict[str, dict] = {}   # last listing, by Facet path
        self.elevation = _elevation         # (lat, lon) -> metres or None; replaced in tests

    # ------------------------------------------------------------ helpers ---
    def local(self, path: str) -> str:
        return to_local(path, self.path_map)

    def folder_config(self, path: str) -> dict:
        try:
            c = cfgmod.find_config(Path(self.local(path)).parent)
            d = cfgmod.load_config(c)
        except OSError:
            c, d = None, {}
        return {"file": str(c).replace("\\", "/") if c else None, "clock_offset": d.get("clock_offset"),
                "time_tolerance_min": d.get("time_tolerance_min"), "timezone": d.get("timezone"),
                "solve_source": d.get("solve_source"), "site": d.get("site"), "cull": d.get("cull"),
                "overlay": d.get("overlay") if isinstance(d.get("overlay"), dict) else None}

    def photo_row(self, p: dict, fcfg: dict) -> dict:
        src = gps_source(p)
        site = fcfg.get("site") if isinstance(fcfg.get("site"), dict) else None
        led = self.ledger.get(self.ctx.library, p["path"])
        return {"path": p["path"], "filename": p.get("filename") or p["path"].replace("\\", "/").rsplit("/", 1)[-1],
                "date_taken": p.get("date_taken"), "date_manually_set": bool(p.get("date_manually_set")),
                "category": p.get("category"), "gps": [p.get("gps_latitude"), p.get("gps_longitude")],
                "gps_source": src if src != "none" else ("folder-config" if site else "none"),
                "raw_companion": p.get("raw_companion") if "raw_companion" in p else None,
                "facet_link": self.client.photo_page_url(p["path"]),
                "ledger": {"status": led["status"], "tags": len(led["tags"]), "annotation": led["annotation_via"]}
                if led else None}

    def listing(self, photos: list[dict], **extra) -> dict:
        self.photos = {p["path"]: p for p in photos}
        cfgs: dict = {}
        rows = []
        for p in photos:
            folder = p["path"].replace("\\", "/").rsplit("/", 1)[0]
            if folder not in cfgs:
                cfgs[folder] = self.folder_config(p["path"])
            rows.append(self.photo_row(p, cfgs[folder]))
        return {"photos": rows, "folders": cfgs, "raw_companion_field": any("raw_companion" in p for p in photos),
                **extra}

    def session_opts(self, body: dict, paths: list[str]):
        from ..session import SessionOptions

        hints = {}
        for fp in paths:
            ph = self.photos.get(fp) or self.client.photo(fp) or {"path": fp}
            self.photos[fp] = ph
            hints[self.local(fp)] = facet_hints(ph)
        tol = body.get("tolerance_min")
        opts = SessionOptions(offset=(body.get("offset") if body.get("offset") not in (None, "") else None),
                              tolerance_min=float(tol) if tol not in (None, "") else None,
                              source=body.get("source") or None, tz=body.get("tz") or None,
                              site=body.get("site") or None, min_confidence=int(body.get("min_confidence") or 65),
                              hints=hints, cull=body.get("cull") or None, overlay_dir=body.get("overlay_dir") or None,
                              no_overlay=bool(body.get("no_overlay")), include_results=False,
                              overlay_scores=bool(body.get("overlay_scores")),
                              overlay_lines=body.get("overlay_lines", True) is not False)
        opts.validate()
        return opts

    # ----------------------------------------------------------- handlers ---
    def state(self, _q, _b):
        return {"facet": self.client.url, "version": self.ctx.version, "annotations": self.ctx.mode,
                "warnings": self.ctx.warnings + self.notes, "can_write": self.client.can_login,
                "ledger": str(self.ledger.path), "default_rule": DEFAULT_RULE, "review_tag": TAG_REVIEW,
                "addon": self.addon_id, "mount": self.mount, "tags_via": self.ctx.tags_via,
                "transport": getattr(self.client, "mode", "api"), "last_site": self.last_site()}

    def albums(self, _q, _b):
        try:
            rows = self.client.albums()
            # A smart album is a saved filter the gallery applies client-side: Facet's album_id scope (and set_gps by
            # album) only sees fixed members, so a smart album would always load 0 photos. Listed as a count only.
            fixed = [a for a in rows if not a.get("is_smart")]
            return {"albums": [{"id": a.get("id"), "name": a.get("name"), "count": a.get("photo_count")} for a in fixed],
                    "smart_skipped": len(rows) - len(fixed)}
        except FacetError as exc:
            return {"albums": [], "error": str(exc)}

    def list_photos(self, _q, b):
        photos = self.client.list_photos(path_prefix=b.get("path_prefix") or None, category=b.get("category") or None,
                                         tag=b.get("tag") or None, album_id=b.get("album_id") or None,
                                         limit=int(b.get("limit") or MAX_SCOPE_PHOTOS))
        return self.listing(photos, scope={"kind": "manual"})

    def scope(self, _q, b):
        """The step-1 photo set from the Facet scope the sidebar was opened with (bridge ``context.scope``):
        ``import`` -> ``GET /api/photos?scan_run=<run_id>`` (+ the page's category filter); ``view`` -> the
        gallery query; ``selection`` -> those paths (<= 500)."""
        kind = b.get("kind")
        if kind == "import":
            run = str(b.get("run_id") if b.get("run_id") is not None else "")
            if not RUN_ID_RE.match(run):
                raise ValueError("import scope needs a run_id")
            photos = self.client.list_photos(category=b.get("category") or None, extra={"scan_run": run},
                                             limit=MAX_SCOPE_PHOTOS)
            return self.listing(photos, scope={"kind": "import", "run_id": run, "category": b.get("category") or None})
        if kind == "view":
            q = b.get("query")
            if q is None:
                q = ""
            if isinstance(q, dict):
                items = list(q.items())
            elif isinstance(q, str):
                if len(q) > MAX_QUERY:
                    raise ValueError("view query is longer than 2 KB")
                items = parse_qsl(q.lstrip("?"), keep_blank_values=False)
            else:
                raise ValueError("view query must be a string or an object")
            if len(json.dumps(items)) > MAX_QUERY * 2:
                raise ValueError("view query is too long")
            extra: dict = {}
            for k, v in items:
                if not isinstance(k, str) or k in SCOPE_DROP:
                    continue
                if isinstance(v, (list, tuple)):
                    v = [str(x) for x in v if isinstance(x, (str, int, float)) and not isinstance(x, bool)]
                elif isinstance(v, bool) or not isinstance(v, (str, int, float)) or v == "":
                    continue
                if k in extra:      # a repeated key in a query string
                    prev = extra[k] if isinstance(extra[k], list) else [extra[k]]
                    v = prev + (v if isinstance(v, list) else [v])
                extra[k] = v
            photos = self.client.list_photos(extra=extra, limit=MAX_SCOPE_PHOTOS)
            return self.listing(photos, scope={"kind": "view", "keys": sorted(extra)})
        if kind == "selection":
            paths = b.get("paths")
            if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
                raise ValueError("selection scope needs a list of paths")
            if len(paths) > MAX_SELECTION:
                raise ValueError(f"selection is limited to {MAX_SELECTION} photos")
            photos = [ph for ph in (self.client.photo(p) for p in dict.fromkeys(paths)) if ph]
            return self.listing(photos, scope={"kind": "selection", "requested": len(paths)})
        raise ValueError("scope kind must be import, view or selection")

    def thumb(self, q, _b):
        """(bytes, content type) of a photo in the current set, fetched by this backend (``photos.read``)."""
        path = (q.get("path") or [""])[0]
        if path not in self.photos:
            raise KeyError("not in the current photo set")
        size = (q.get("size") or [""])[0]
        return self.client.thumbnail(path, int(size) if size.isdigit() and 16 <= int(size) <= 640 else None)

    # ------------------------------------------------------- location (R-LOC) ---
    def _folders_in_set(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.photos:
            f = p.replace("\\", "/").rsplit("/", 1)[0]
            out[f] = out.get(f, 0) + 1
        return out

    def last_site(self) -> dict | None:
        raw = self.ledger.get_meta(self.ctx.library, LAST_SITE)
        try:
            site = json.loads(raw) if raw else None
        except ValueError:
            return None
        return site if isinstance(site, dict) and {"lat", "lon"} <= set(site) else None

    def location(self, _q, b):
        """One location for a whole folder or album (PLUGIN_BOUNDARY.md v0.3 R-LOC), never one photo.

        ``{scope: {folder} | {album_id}, text, overwrite?, confirm?, name?}``. Without ``confirm``: parse the text
        (Google Earth / Maps URL, decimal ``lat, lon[, elev]`` or DMS) and count what would change (a dry run of
        ``set_gps``). With ``confirm``: write it, remember it as the last site, and save it as the site of each
        local folder in the scope (``astro-id.json``) for plate solving."""
        from ..site import parse_location

        scope = b.get("scope") if isinstance(b.get("scope"), dict) else {}
        folder, album = scope.get("folder"), scope.get("album_id")
        if (folder is None) == (album is None):
            raise ValueError("pick one folder or one album; a location is never set photo by photo")
        if b.get("path") is not None or b.get("paths") is not None:
            raise ValueError("a location is set for a folder or an album, not for single photos")
        if folder is not None:
            if not isinstance(folder, str) or folder not in self._folders_in_set():
                raise ValueError("that folder is not in the current photo set")
            kw: dict = {"path_prefix": folder}
            name = folder.rsplit("/", 1)[-1] or folder
            scope_out = {"kind": "folder", "folder": folder, "name": name}
        else:
            if isinstance(album, bool) or not str(album).isdigit():
                raise ValueError("album_id must be a number")
            album = int(album)
            kw = {"album_id": album}
            names = {int(a["id"]): a.get("name") for a in self.client.albums() if a.get("id") is not None}
            if album not in names:
                raise ValueError("no such album")
            name = names[album] or f"album {album}"
            scope_out = {"kind": "album", "album_id": album, "name": name}
        lat, lon, elev = parse_location(str(b.get("text") or ""))
        if elev is None:
            elev = self.elevation(lat, lon)
        confirm = bool(b.get("confirm"))
        r = self.client.set_location(lat, lon, overwrite=bool(b.get("overwrite")), dry_run=not confirm, **kw)
        out = {"lat": lat, "lon": lon, "elev": elev, "scope": scope_out, "overwrite": bool(b.get("overwrite")),
               "changed": r.get("changed", 0), "skipped": r.get("skipped", 0), "confirmed": confirm}
        out["photos"] = out["changed"] + out["skipped"]
        if confirm:
            site = {"name": str(b.get("name") or name)[:120], "lat": lat, "lon": lon, "elev_m": elev}
            self.ledger.set_meta(self.ctx.library, LAST_SITE, json.dumps(site))
            out["saved"] = self._save_site(kw, site)
            for p in list(self.photos):     # re-read what changed, so the table shows the new source
                if folder is None or p.replace("\\", "/").rsplit("/", 1)[0] == folder:
                    self.photos[p] = self.client.photo(p) or self.photos[p]
        return out

    def _save_site(self, kw: dict, site: dict) -> list[str]:
        """Write ``site`` into ``astro-id.json`` of every local folder of the scope (existing keys kept)."""
        from ..config import CONFIG_NAME, Site
        from ..site import merge_config

        if "path_prefix" in kw:
            facet_folders = {kw["path_prefix"]}
        else:
            facet_folders = {p["path"].replace("\\", "/").rsplit("/", 1)[0]
                             for p in self.client.list_photos(album_id=kw["album_id"], limit=MAX_SCOPE_PHOTOS)}
        written = []
        for ff in sorted(facet_folders):
            local = Path(self.local(ff + "/x")).parent
            if not local.is_dir():
                continue
            path = local / CONFIG_NAME
            try:
                existing = cfgmod.load_config(path) if path.exists() else {}
            except (OSError, ValueError):
                existing = {}
            merged = merge_config(existing, Site(site["lat"], site["lon"], site["elev_m"], site["name"]))
            path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            written.append(str(path).replace("\\", "/"))
        return written

    def preview(self, _q, b):
        from ..session import time_preview

        paths = list(b.get("paths") or [])
        opts = self.session_opts(b, paths)
        back = {self.local(p): p for p in paths}
        rows = time_preview(list(back), opts)
        for r in rows:
            r["path"] = back.get(r["path"], r["path"])
        return {"preview": rows}

    def save_config(self, _q, b):
        from ..session import save_folder_config

        paths = list(b.get("paths") or [])
        opts = self.session_opts(b, paths)
        extra = {}
        ov = b.get("overlay")
        if isinstance(ov, dict):
            extra["overlay"] = {k: bool(ov.get(k)) for k in ("on", "lines", "scores")}
        return {"written": save_folder_config(sorted({str(Path(self.local(p)).parent) for p in paths}), opts, extra)}

    def solve(self, _q, b):
        paths = list(b.get("paths") or [])
        if not paths:
            raise ValueError("no photos selected")
        if self.job.running:
            raise ValueError("a solve is already running")
        opts = self.session_opts(b, paths)
        write = bool(b.get("write", True))
        self.job = job = Job()
        job.min_confidence = opts.min_confidence
        job.running = True
        threading.Thread(target=self._run, args=(job, paths, opts, write), name="astro-solve", daemon=True).start()
        return {"started": len(paths), "write": write}

    def _run(self, job: Job, paths: list[str], opts, write: bool) -> None:
        from ..session import run_session

        try:
            back = {self.local(p): p for p in paths}
            done = run_session(list(back), opts, job.emit)
            job.results = {back[lp]: fr for lp, fr in (done.get("results") or {}).items() if lp in back}
            job.done = {k: v for k, v in done.items() if k != "results"}
            job.done["cull"]["frames"] = {back.get(k, k): v for k, v in job.done["cull"]["frames"].items()}
            job.done["quality"] = {fp: fr.get("quality") for fp, fr in job.results.items()}
            job.done["solved_by_path"] = {fp: bool(fr.get("solved")) for fp, fr in job.results.items()}
            if write:
                job.emit({"event": "writing", "photos": len(job.results)})
                job.reports = apply_results(self._ctx_for(job), job.results, emit=job.emit)
        except Exception as exc:   # surfaced to the page
            log.exception("solve job failed")
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.running = False

    def _ctx_for(self, job: Job) -> SyncContext:
        """The writer with the session's own tag cut (the page's "Min confidence")."""
        from dataclasses import replace

        return replace(self.ctx, min_confidence=int(job.min_confidence))

    def job_state(self, q, _b):
        return self.job.snapshot(int((q.get("after") or ["0"])[0]))

    def recull(self, _q, b):
        from ..session import recull

        rule = b.get("rule")
        errs = validate_rule(rule)
        if errs:
            raise ValueError("; ".join(errs))
        if not self.job.done:
            raise ValueError("solve first")
        r = recull(self.job.done["quality"], rule, self.job.done["solved_by_path"])
        return {"reference": r["reference"], "rule": r["rule"],
                "frames": {p: {"decision": v["decision"], "reasons": v["reasons"]} for p, v in r["frames"].items()}}

    def review_tags(self, _q, b):
        """Add ``astro-review`` to the frames listed (review/reject verdicts the user chose) and remove it from the
        others of this session; every other astro-id tag is re-asserted unchanged."""
        if not self.job.results:
            raise ValueError("solve first")
        flagged = set(b.get("paths") or [])
        extra = {p: ([TAG_REVIEW] if p in flagged else []) for p in self.job.results}
        reports = apply_results(self._ctx_for(self.job), self.job.results, extra_tags=extra)
        return {"reports": reports}

    ROUTES = {("GET", "api/state"): "state", ("GET", "api/albums"): "albums", ("GET", "api/job"): "job_state",
              ("POST", "api/photos"): "list_photos", ("POST", "api/scope"): "scope", ("POST", "api/location"): "location",
              ("POST", "api/preview"): "preview", ("POST", "api/save-config"): "save_config",
              ("POST", "api/solve"): "solve", ("POST", "api/recull"): "recull",
              ("POST", "api/review-tags"): "review_tags"}
    BINARY = {("GET", "api/thumb"): "thumb"}


class _Base(BaseHTTPRequestHandler):
    """Shared transport: JSON helpers, POST body parsing, route dispatch. No cookies anywhere."""

    server_version = "astro-id"
    csp = STANDALONE_CSP
    app_box: dict = {}

    @property
    def app(self) -> App | None:
        return self.app_box.get("app")

    def log_message(self, fmt, *args):   # quiet; secrets may be in the path
        log.debug("serve: " + fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", self.csp)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, doc) -> None:
        self._send(code, json.dumps(doc, default=str).encode("utf-8"), "application/json")

    def _read_json(self):
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._send(415, b"json only", "text/plain")
            return None
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            self._json(400, {"error": "bad JSON"})
            return None
        return body if isinstance(body, dict) else {}

    def _dispatch(self, method: str, rel: str, body: dict) -> None:
        app = self.app
        if app is None:
            return self._json(503, {"error": "starting"})
        q = parse_qs(urlparse(self.path).query)
        binary = App.BINARY.get((method, rel))
        name = App.ROUTES.get((method, rel))
        if name is None and binary is None:
            return self._send(404, b"not found", "text/plain")
        try:
            if binary:
                data, ctype = getattr(app, binary)(q, body)
                return self._send(200, data, ctype)
            self._json(200, getattr(app, name)(q, body))
        except KeyError as exc:
            if binary:
                return self._json(404, {"error": str(exc).strip("'\"")})
            self._json(400, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except FacetError as exc:
            self._json(502, {"error": str(exc), "status": exc.status})
        except Exception as exc:   # pragma: no cover - surfaced to the page
            log.exception("serve: %s failed", rel)
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _loopback_host(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host not in LOOPBACK:
            self._send(403, b"forbidden", "text/plain")
            return False
        return True


def make_handler(app: App, secret: str):
    """Standalone mode: the per-start secret path prefix, loopback Host, no foreign Origin on writes."""
    prefix = f"/{secret}/"

    class Handler(_Base):
        app_box = {"app": app}

        def _guard(self) -> str | None:
            if not self._loopback_host():
                return None
            u = urlparse(self.path)
            if not u.path.startswith(prefix) and u.path != prefix.rstrip("/"):
                self._send(404, b"not found", "text/plain")
                return None
            return u.path[len(prefix):] if u.path.startswith(prefix) else ""

        def do_GET(self):
            rel = self._guard()
            if rel is None:
                return
            if rel in ("", "index.html"):
                return self._send(200, page_html(), "text/html; charset=utf-8")
            self._dispatch("GET", rel, {})

        def do_POST(self):
            rel = self._guard()
            if rel is None:
                return
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                return self._send(403, b"forbidden origin", "text/plain")
            body = self._read_json()
            if body is not None:
                self._dispatch("POST", rel, body)

    return Handler


def make_addon_handler(app_box: dict, addon_id: str, launch_secret: str):
    """Add-on mode (spec §5/§8): ``/health`` challenge/response; everything else needs ``X-Facet-Launch``; the UI
    and its API live under ``/ui/``."""

    class Handler(_Base):
        csp = ADDON_CSP

        def _route(self) -> str | None:
            """The decoded path after the gates, or None when already answered."""
            if not self._loopback_host():
                return None
            p = safe_rel_path(urlparse(self.path).path)
            if p is None:
                self._send(400, b"bad path", "text/plain")
                return None
            if p == "/health" and self.command in ("GET", "HEAD"):
                c = (parse_qs(urlparse(self.path).query).get("c") or [""])[0]
                if not 1 <= len(c) <= 512:
                    self._json(400, {"error": "challenge c required"})
                    return None
                self._json(200, {"addon": addon_id, "ack": health_ack(launch_secret, c)})
                return None
            got = self.headers.get("X-Facet-Launch") or ""
            if not hmac.compare_digest(got.encode("utf-8"), launch_secret.encode("utf-8")):
                self._send(403, b"forbidden", "text/plain")
                return None
            pre = (self.headers.get("X-Forwarded-Prefix") or "").rstrip("/")
            if pre and PREFIX_RE.match(pre) and self.app is not None:
                self.app.mount = pre
            return p

        def do_GET(self):
            p = self._route()
            if p is None:
                return
            if p == "/ui":   # a relative Location: resolves under whatever prefix the proxy mounts us at
                return self._send(308, b"", "text/plain", {"Location": "ui/"})
            if p in ("/ui/", "/ui/index.html"):
                return self._send(200, page_html(), "text/html; charset=utf-8")
            if p.startswith("/ui/api/"):
                return self._dispatch("GET", p[len("/ui/"):], {})
            self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def do_POST(self):
            p = self._route()
            if p is None:
                return
            if not p.startswith("/ui/api/"):
                return self._send(404, b"not found", "text/plain")
            body = self._read_json()
            if body is not None:
                self._dispatch("POST", p[len("/ui/"):], body)

        def do_OPTIONS(self):
            if self._route() is None:
                return
            self._send(204, b"", "text/plain", {"Allow": "GET, HEAD, POST, OPTIONS"})

        def _refuse(self):
            if self._route() is None:
                return
            self._send(405, b"method not allowed", "text/plain")

        do_PUT = do_DELETE = do_PATCH = _refuse

    Handler.app_box = app_box
    return Handler


def build(args, client: FacetClient | None = None, ledger: Ledger | None = None, secret: str | None = None):
    """Standalone: (server, url, app); the caller runs ``server.serve_forever()``."""
    client = client or client_from_env(getattr(args, "facet_url", None))
    ledger = ledger or Ledger(getattr(args, "ledger", None))
    ctx = SyncContext(client=client, ledger=ledger, legacy_db=getattr(args, "legacy_db", None),
                      facet_dir=getattr(args, "facet_dir", None),
                      annotations=not getattr(args, "no_annotations", False)).prepare()
    app = App(client, ledger, ctx, parse_path_map(getattr(args, "path_map", None)))
    secret = secret or secrets.token_urlsafe(18)
    server = ThreadingHTTPServer(("127.0.0.1", int(getattr(args, "port", 0) or 0)), make_handler(app, secret))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/{secret}/"
    return server, url, app


class AddonEnv:
    """What Facet's supervisor passes in the clean environment (spec §5)."""

    def __init__(self, env: dict | None = None):
        e = os.environ if env is None else env
        self.url = (e.get("FACET_URL") or "").strip()
        self.addon_id = (e.get("FACET_ADDON_ID") or "").strip()
        self.secret = e.get("FACET_LAUNCH_SECRET") or ""
        port = (e.get("FACET_PORT") or "").strip()
        missing = [k for k, v in (("FACET_URL", self.url), ("FACET_ADDON_ID", self.addon_id),
                                  ("FACET_LAUNCH_SECRET", self.secret)) if not v]
        if missing:
            raise ValueError("add-on mode needs " + ", ".join(missing) + " from Facet (it starts the add-on)")
        if len(self.secret) < 16:
            raise ValueError("FACET_LAUNCH_SECRET is too short")
        if port and not port.isdigit():
            raise ValueError("FACET_PORT must be a port number")
        self.port = int(port) if port else 0
        self.credentials, self.credential_note = addon_credentials(self.addon_id, e)


def build_addon(args, env: dict | None = None, client: FacetClient | None = None, ledger: Ledger | None = None):
    """Add-on mode, step 1: bind the socket (nothing answers until ``finish_addon``). Returns (server, box, aenv)."""
    if getattr(args, "legacy_db", None):
        raise ValueError("--legacy-db is not available in add-on mode (annotations go through Facet's API or are "
                         "skipped)")
    aenv = AddonEnv(env)
    if aenv.addon_id != ADDON_ID:
        log.warning("Facet started this add-on as %r; its manifest id is %r", aenv.addon_id, ADDON_ID)
    box: dict = {"client": client, "ledger": ledger}
    server = ThreadingHTTPServer(("127.0.0.1", aenv.port), make_addon_handler(box, aenv.addon_id, aenv.secret))
    server.daemon_threads = True
    return server, box, aenv


def finish_addon(args, box: dict, aenv: AddonEnv) -> App:
    """Add-on mode, step 2 (after the port line): connect to Facet and build the session app."""
    client = box.get("client") or connect(aenv.url, **aenv.credentials)
    ledger = box.get("ledger") or Ledger(getattr(args, "ledger", None))
    ctx = SyncContext(client=client, ledger=ledger, addon=True,
                      annotations=not getattr(args, "no_annotations", False)).prepare()
    app = App(client, ledger, ctx, parse_path_map(getattr(args, "path_map", None)), addon_id=aenv.addon_id)
    if aenv.credential_note:
        app.notes.append(aenv.credential_note)
        log.warning("%s", aenv.credential_note)
    box["app"] = app
    return app


def serve_addon(args, env: dict | None = None, out=None, lifeline: bool = True) -> int:
    out = out or sys.stdout
    server, box, aenv = build_addon(args, env)
    out.write(f"FACET_ADDON_PORT={server.server_address[1]}\n")
    out.flush()
    if lifeline:
        from .lifeline import watch

        log.info("lifeline: exits when any of %s ends", watch() or "nothing (not watched)")
    app = None
    try:
        app = finish_addon(args, box, aenv)
        log.info("astro-id add-on %s on 127.0.0.1:%d for Facet %s (version %s); annotations: %s",
                 aenv.addon_id, server.server_address[1], app.client.url, app.ctx.version, app.ctx.mode or "skipped")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if app is not None:
            app.ctx.close()
            app.ledger.close()
    return 0


def serve(args) -> int:
    if getattr(args, "addon", False):
        return serve_addon(args)
    server, url, app = build(args)
    print(f"astro-id session UI: {url}\nFacet: {app.client.url} (version {app.ctx.version}); "
          f"annotations: {app.ctx.mode or 'skipped'}; Ctrl+C to stop", flush=True)
    for w in app.ctx.warnings:
        print(f"note: {w}", flush=True)
    if not getattr(args, "no_browser", False):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.ctx.close()
        app.ledger.close()
    return 0


__all__ = ["build", "build_addon", "finish_addon", "serve", "serve_addon", "health_ack", "safe_rel_path",
           "merge_rule"]
