"""``astro-id facet serve``: a small local web UI for an astro session against a Facet library.

Binds 127.0.0.1 only; every URL carries a random per-start secret (``http://127.0.0.1:<port>/<secret>/``), any other
path is 404, a request whose Host is not loopback is refused (DNS rebinding) and writes need a JSON body with no
foreign Origin. Plain stdlib ``http.server`` + one static HTML page, no build step.

Flow (one page, five steps): pick photos (Facet path prefix / album / tag) -> location (each photo's GPS source;
set a missing one with a Google Maps paste through ``PUT /api/photo/gps``, or open the photo in Facet) -> confirm
time (camera time, corrected time, offset, tolerance, timezone, RAW/JPG source; ``time_preview``) -> plate solve
(background thread, live JSON-lines events, then the sync layer writes tags + annotations) -> culling (quality per
frame, keep/review/reject with reasons, editable rule, re-cull without re-solving). Facet's own reject / cull flags
are never written; the optional ``astro-review`` tag (off by default) is a ledger-owned tag like the others.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config as cfgmod
from ..cull import DEFAULT_RULE, merge_rule, validate_rule
from .client import FacetClient, FacetError
from .ledger import Ledger
from .sync import (TAG_REVIEW, SyncContext, apply_results, facet_hints, gps_source, parse_path_map, to_local)

log = logging.getLogger("astro_id.facet")
STATIC = Path(__file__).with_name("static")
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


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
    def __init__(self, client: FacetClient, ledger: Ledger, ctx: SyncContext, path_map):
        self.client, self.ledger, self.ctx, self.path_map = client, ledger, ctx, path_map
        self.job = Job()
        self.photos: dict[str, dict] = {}   # last listing, by Facet path

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
                "solve_source": d.get("solve_source"), "site": d.get("site"), "cull": d.get("cull")}

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
                              no_overlay=bool(body.get("no_overlay")), include_results=False)
        opts.validate()
        return opts

    # ----------------------------------------------------------- handlers ---
    def state(self, _q, _b):
        return {"facet": self.client.url, "version": self.ctx.version, "annotations": self.ctx.mode,
                "warnings": self.ctx.warnings, "can_write": self.client.can_login, "ledger": str(self.ledger.path),
                "default_rule": DEFAULT_RULE, "review_tag": TAG_REVIEW}

    def albums(self, _q, _b):
        try:
            return {"albums": [{"id": a.get("id"), "name": a.get("name"), "count": a.get("photo_count")}
                               for a in self.client.albums()]}
        except FacetError as exc:
            return {"albums": [], "error": str(exc)}

    def list_photos(self, _q, b):
        photos = self.client.list_photos(path_prefix=b.get("path_prefix") or None, category=b.get("category") or None,
                                         tag=b.get("tag") or None, album_id=b.get("album_id") or None,
                                         limit=int(b.get("limit") or 2000))
        self.photos = {p["path"]: p for p in photos}
        cfgs: dict = {}
        rows = []
        for p in photos:
            folder = p["path"].replace("\\", "/").rsplit("/", 1)[0]
            if folder not in cfgs:
                cfgs[folder] = self.folder_config(p["path"])
            rows.append(self.photo_row(p, cfgs[folder]))
        return {"photos": rows, "folders": cfgs, "raw_companion_field": any("raw_companion" in p for p in photos)}

    def set_gps(self, _q, b):
        from ..site import parse_latlon

        lat, lon = parse_latlon(str(b.get("text") or ""))
        r = self.client.put_gps(b["path"], lat, lon)
        ph = self.client.photo(b["path"]) or {}
        self.photos[b["path"]] = ph
        return {"path": b["path"], "gps": [r.get("gps_latitude"), r.get("gps_longitude")], "gps_source": gps_source(ph)}

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
        return {"written": save_folder_config(sorted({str(Path(self.local(p)).parent) for p in paths}), opts)}

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
              ("POST", "api/photos"): "list_photos", ("POST", "api/gps"): "set_gps",
              ("POST", "api/preview"): "preview", ("POST", "api/save-config"): "save_config",
              ("POST", "api/solve"): "solve", ("POST", "api/recull"): "recull",
              ("POST", "api/review-tags"): "review_tags"}


def make_handler(app: App, secret: str):
    prefix = f"/{secret}/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "astro-id"

        def log_message(self, fmt, *args):   # quiet; the secret is in every path
            log.debug("serve: " + fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, doc) -> None:
            self._send(code, json.dumps(doc, default=str).encode("utf-8"), "application/json")

        def _guard(self) -> str | None:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            if host not in LOOPBACK:
                self._send(403, b"forbidden", "text/plain")
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
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            self._dispatch("GET", rel, {})

        def do_POST(self):
            rel = self._guard()
            if rel is None:
                return
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                return self._send(403, b"forbidden origin", "text/plain")
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, b"json only", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            self._dispatch("POST", rel, body if isinstance(body, dict) else {})

        def _dispatch(self, method, rel, body):
            name = App.ROUTES.get((method, rel))
            if name is None:
                return self._send(404, b"not found", "text/plain")
            try:
                self._json(200, getattr(app, name)(parse_qs(urlparse(self.path).query), body))
            except (ValueError, KeyError) as exc:
                self._json(400, {"error": str(exc)})
            except FacetError as exc:
                self._json(502, {"error": str(exc), "status": exc.status})
            except Exception as exc:   # pragma: no cover - surfaced to the page
                log.exception("serve: %s failed", rel)
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def build(args, client: FacetClient | None = None, ledger: Ledger | None = None, secret: str | None = None):
    """(server, url, app); the caller runs ``server.serve_forever()``."""
    client = client or FacetClient.from_env(getattr(args, "facet_url", None))
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


def serve(args) -> int:
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


__all__ = ["build", "serve", "merge_rule"]
