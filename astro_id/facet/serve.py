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
  The process Facet starts is a small keeper (keeper.py) and this server runs in its worker, so the add-on can
  restart itself on new code (``POST api/restart``) on the same port and launch secret, the session kept.

Flow (one page, two sections):

- **A. Setup for this folder or album**: photos (Facet's scope in add-on mode, else a folder or an album), location
  (one site for the folder / album, R-LOC), time offset, JPG/RAW. ``api/save-config`` writes it to every folder's
  ``astro-id.json`` and, for an album, to the album setup store (the ledger's meta table, key ``album_setup:<id>``
  per Facet library).
- **B. Run**: plate solve (background thread, JSON-lines events; it writes **nothing** to Facet) -> the results
  review in the panel (``api/job`` ``done.review``) -> ``api/save`` writes object tags + sky labels (annotations) for the
  last solve's results -> culling (low priority; quality, keep/review/reject, re-cull without re-solving). The
  sky-label view (groups, min confidence, max magnitude, scores) is a display setting the panel sends to Facet over
  the bridge; ``api/annotations`` reads a photo's stored astro-id overlay so the panel can offer the controls.

Terms: **tags** = words that describe a photo (astro-id adds only 1-3 status tags); **object tags** = the sky objects
astro-id names in a photo (Facet's search and filters find them; an older Facet stores them as ordinary tags);
**sky labels** = names Facet draws over the photo (the
annotation payload); **labeled copies** = separate JPG files with the labels burned in (was "overlay images").
Facet's own reject / cull flags are never written.
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
from . import keeper
from .client import ALL_FRAMES, FacetClient, FacetError, addon_credentials, client_from_env, connect
from .ledger import Ledger
from .sync import (ANNOTATION_SOURCE, TAG_REVIEW, SyncContext, apply_results, facet_hints, gps_source,
                   parse_path_map, to_local)

log = logging.getLogger("astro_id.facet")
STATIC = Path(__file__).with_name("static")
LAST_SITE = "last_site"
ALBUM_SETUP = "album_setup:"          # ledger meta key prefix: one album's saved setup (JSON), per Facet library
COPY_KEYS = ("on", "lines", "scores")
EXPORT_KEYS = ("lines", "scores", "legend")   # Export's plain-language options (panel step 6)
# The page/server contract version. index.html embeds the same string (const PAGE_VERSION); Facet's supervisor keeps
# an add-on process running while the page is read from disk on every load, so after an update the page can be newer
# than its server. The panel then asks the server to restart itself on the new code (api/restart, keeper.py); only
# a server too old for that gets the one-line "click Stop, then Open". Bump both together.
PAGE_VERSION = "2026-11-14.2"
PACKAGE = Path(__file__).resolve().parents[1]    # the astro_id package: its .py files are what a restart reloads
UPDATING = "Astro is updating: try again in a moment"


def code_stamp(root: Path = PACKAGE) -> str:
    """A fingerprint of the add-on's Python code on disk (every ``.py`` path, size and mtime): it changes when any
    of it is edited, added or removed. The page and bridge are read from disk per request, so they are not in it."""
    h = sha256()
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root).as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


START_STAMP = code_stamp()   # taken when this module loaded, i.e. on the code this process runs


def disk_page_version(path: Path | None = None) -> str | None:
    """The PAGE_VERSION of the page on disk now (what a panel opened or reloaded now gets), or None."""
    try:
        text = (path or STATIC / "index.html").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'const PAGE_VERSION = "([^"]+)";', text)
    return m.group(1) if m else None


def restart_decision(supported: bool, code_changed: bool, busy: str | None, force: bool = False) -> dict:
    """Whether the running add-on restarts itself now. ``busy``: "solve" / "export" (a run in progress: never cut
    it, the panel asks again when it ends). Without new code on disk a restart would change nothing (``current``),
    unless ``force``."""
    if not supported:
        return {"restart": False, "reason": "unsupported"}
    if busy:
        return {"restart": False, "reason": "busy", "busy": busy}
    if not (code_changed or force):
        return {"restart": False, "reason": "current"}
    return {"restart": True}


def _plain(o):
    """json.dumps default for the handoff file: numpy scalars/arrays, paths and sets; anything else is refused."""
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"{type(o).__name__} is not JSON")


class SelfUpdate:
    """This server's view of its own code (add-on worker mode only): started on which code, may it restart."""

    def __init__(self, supported: bool, handoff: Path | None = None, root: Path = PACKAGE):
        self.supported = supported
        self.handoff = handoff
        self.root = root
        self.boot = secrets.token_hex(6)       # changes on every restart: the panel waits for a new one
        self.stamp = START_STAMP if root == PACKAGE else code_stamp(root)
        self.exit = None                       # callable: stop serving and exit with keeper.RESTART_EXIT

    def code_changed(self) -> bool:
        return code_stamp(self.root) != self.stamp


def export_options(v) -> dict | None:
    """Saved Export options ({lines, scores, legend} booleans), or None."""
    return {k: v[k] for k in EXPORT_KEYS if isinstance(v.get(k), bool)} if isinstance(v, dict) else None


def _base(p) -> str:
    return str(p).replace(chr(92), "/").rsplit("/", 1)[-1]


def time_fit(results: dict, offset) -> dict:
    """Auto time: what the sky says about the camera clock, from the frames whose result pins the time
    (``best_fit_delta_min``; today only the Moon does). The median shift is added to the offset the solve used and
    rounded to whole minutes; the tolerance covers the spread between those frames plus 2 min. Nothing is
    suggested when no frame pins the time."""
    import math
    from datetime import timedelta
    from statistics import median

    from ..timeutil import format_offset, parse_offset

    pins = sorted((_base(p), float(fr["best_fit_delta_min"])) for p, fr in (results or {}).items()
                  if isinstance(fr, dict) and fr.get("solved") and fr.get("solver") != "stack-copy"
                  and isinstance(fr.get("best_fit_delta_min"), (int, float)))
    out = {"frames": len(pins), "pinned_by": "the Moon", "files": [f for f, _ in pins][:20], "offset": None,
           "delta_min": None, "spread_min": None, "tolerance_min": None}
    if not pins:
        return out
    try:
        base = parse_offset(offset if offset not in (None, "") else None)
    except ValueError:
        return out
    ds = [d for _, d in pins]
    med = median(ds)
    spread = max(abs(d - med) for d in ds)
    out.update(delta_min=round(med, 1), spread_min=round(spread, 1),
               offset=format_offset(base + timedelta(minutes=round(med))), tolerance_min=int(math.ceil(spread + 2)))
    return out


def labeled_copies(d: dict) -> dict | None:
    """The labeled-copies settings of a folder / album setup; an older ``astro-id.json`` has them as ``overlay``."""
    v = d.get("labeled_copies")
    if not isinstance(v, dict):
        v = d.get("overlay")
    return {k: bool(v.get(k)) for k in COPY_KEYS if k in v} if isinstance(v, dict) else None


def sky_view(v) -> dict | None:
    """A saved sky-label view (never ``visible``: labels always start hidden), keys and types checked."""
    if not isinstance(v, dict):
        return None
    out: dict = {}
    g = v.get("groups")
    if isinstance(g, dict):
        out["groups"] = {str(k)[:40]: x for k, x in list(g.items())[:20] if isinstance(x, bool)}
    if "score" in v and (v["score"] is None or (isinstance(v["score"], (int, float))
                                                and not isinstance(v["score"], bool))):
        out["score"] = v["score"]
    m = v.get("metrics")
    if isinstance(m, dict):
        out["metrics"] = {str(k)[:40]: x for k, x in list(m.items())[:10]
                          if isinstance(x, (int, float)) and not isinstance(x, bool)}
    for k in ("show_scores", "show_members"):
        if isinstance(v.get(k), bool):
            out[k] = v[k]
    return out


def review_rows(paths: list[str], results: dict) -> list[dict]:
    """The panel's results review: per photo solved?, grade, and the objects found (name, confidence, group),
    best first. Compact: no geometry."""
    from ..annotations import LEVEL_GROUP
    from .sync import normalize_tag

    rows = []
    for p in paths:
        fr = results.get(p) or {}
        # "items": how many sky-label items of this photo carry the name. Facet can only index a name that has one
        # (and accepts it as a tag-like word), so the panel counts searchable names from this, not from the objects.
        labels: dict[str, int] = {}
        for it in (fr.get("annotations") or {}).get("items") or []:
            if isinstance(it, dict) and isinstance(it.get("label"), str) and normalize_tag(it["label"]):
                labels[it["label"]] = labels.get(it["label"], 0) + 1
        objs = [{"name": o.get("name") or o.get("tag"), "confidence": o.get("confidence"),
                 "group": LEVEL_GROUP.get(o.get("level")), "tag": o.get("taggable", True) is not False,
                 "items": labels.get(o.get("name") or o.get("tag"), 0)}
                for o in fr.get("objects") or [] if isinstance(o, dict)]
        objs.sort(key=lambda o: (-(o["confidence"] or 0), str(o["name"])))
        status = "stack-copy" if fr.get("solver") == "stack-copy" else ("solved" if fr.get("solved") else "failed")
        rows.append({"path": p, "status": status, "grade": fr.get("grade"), "error": fr.get("error"),
                     "objects": objs})
    return rows


def _confidence(v) -> int | None:
    """A tag cut (0-100) from a page or a config file, or None when absent or not a number."""
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return None


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
        self.saved = False                 # "Save to Facet" ran for these results
        self.saved_paths: set[str] = set() # the photos it wrote (culling's review tag goes only on these)
        self.min_confidence = 65
        self.lock = threading.Lock()

    def emit(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)

    def snapshot(self, after: int) -> dict:
        with self.lock:
            return {"running": self.running, "events": self.events[after:], "next": len(self.events),
                    "done": self.done, "reports": self.reports, "error": self.error, "saved": self.saved}


class App:
    def __init__(self, client: FacetClient, ledger: Ledger, ctx: SyncContext, path_map, addon_id: str | None = None):
        self.client, self.ledger, self.ctx, self.path_map = client, ledger, ctx, path_map
        self.addon_id = addon_id
        self.mount: str | None = None       # X-Forwarded-Prefix from Facet's proxy (informational)
        self.notes: list[str] = []          # start-up notes shown in the page (credential mode etc.)
        self.job = Job()
        self.photos: dict[str, dict] = {}   # last listing, by Facet path
        self.elevation = _elevation         # (lat, lon) -> metres or None; replaced in tests
        self.export_job: dict = {}          # the last Export run (progress for the panel)
        self._export_lock = threading.Lock()  # one export at a time: the check and the start are one step
        self.update = SelfUpdate(False)     # add-on worker mode replaces it (finish_addon)
        self.restarting = False             # set once api/restart accepted: no new solve / export starts

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
                "labeled_copies": labeled_copies(d), "sky_labels": sky_view(d.get("sky_labels")),
                "min_confidence": _confidence(d.get("min_confidence")), "export": export_options(d.get("export"))}

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
        copies = labeled_copies(body)       # also reads an older caller's "overlay"
        if copies is None:                  # older callers: no_overlay / overlay_lines / overlay_scores; default off
            copies = {"on": body.get("no_overlay") is False, "lines": body.get("overlay_lines", True) is not False,
                      "scores": bool(body.get("overlay_scores"))}
        opts = SessionOptions(offset=(body.get("offset") if body.get("offset") not in (None, "") else None),
                              tolerance_min=float(tol) if tol not in (None, "") else None,
                              source=body.get("source") or None, tz=body.get("tz") or None,
                              site=body.get("site") or None, min_confidence=int(body.get("min_confidence") or 65),
                              hints=hints, cull=body.get("cull") or None, overlay_dir=body.get("overlay_dir") or None,
                              no_overlay=not copies.get("on"), include_results=False,
                              overlay_scores=bool(copies.get("scores")),
                              overlay_lines=copies.get("lines", True) is not False)
        opts.validate()
        return opts

    # ----------------------------------------------------------- handlers ---
    def state(self, _q, _b):
        self.ctx.refresh()
        return {"facet": self.client.url, "version": self.ctx.version, "annotations": self.ctx.mode,
                "warnings": self.ctx.warnings + self.notes, "can_write": self.client.can_login,
                "ledger": str(self.ledger.path), "default_rule": DEFAULT_RULE, "review_tag": TAG_REVIEW,
                "addon": self.addon_id, "mount": self.mount, "tags_via": self.ctx.tags_via,
                # 'searchable': names become Facet detections (model D); 'tags': names become tags (older Facet)
                "names_as": "searchable" if self.ctx.model_d else "tags",
                "transport": getattr(self.client, "mode", "api"), "last_site": self.last_site(),
                "sky_vocab": self.sky_vocab(), "page_version": PAGE_VERSION,
                # self-update (keeper.py): the panel restarts an add-on whose code on disk changed
                "update": {"supported": self.update.supported, "boot": self.update.boot,
                           "code_changed": self.update.supported and self.update.code_changed(),
                           "busy": self.busy(), "restarting": self.restarting,
                           "unsaved": bool(self.job.results) and not self.job.saved,
                           "disk_page_version": disk_page_version() if self.update.supported else None}}

    def busy(self) -> str | None:
        """What a restart must wait for: a solve or an export in progress."""
        if self.job.running:
            return "solve"
        if self.export_job.get("running"):
            return "export"
        return None

    # ------------------------------------------------------------ self-update ---
    def restart(self, _q, b):
        """``POST api/restart``: restart this add-on on the code now on disk (keeper.py), keeping the session
        (loaded photos, the last solve's results whether saved or not, the last export) through the handoff file.
        Refused while a solve or export runs (``busy``); the panel asks again when it ends."""
        with self._export_lock:
            d = restart_decision(self.update.supported, self.update.code_changed(), self.busy(),
                                 bool(b.get("force")))
            if not d["restart"]:
                return d
            try:
                self.save_handoff()
            except (OSError, TypeError, ValueError) as exc:
                if self.job.results and not self.job.saved:
                    log.warning("self-update: results not kept (%s); waiting for Save to Facet", exc)
                    return {"restart": False, "reason": "unsaved"}
                log.warning("self-update: session not kept (%s)", exc)
            self.restarting = True
        log.info("self-update: restarting on the code now on disk")
        t = threading.Timer(0.3, self.update.exit)   # after this answer has gone out
        t.daemon = True
        t.start()
        return {"restart": True, "boot": self.update.boot}

    def save_handoff(self) -> None:
        """Write the session to the handoff file (atomic). Raises when it cannot be written as plain JSON."""
        path = self.update.handoff
        if path is None:
            return
        job = self.job
        with job.lock:
            doc = {"page_version": PAGE_VERSION, "photos": self.photos, "mount": self.mount,
                   "job": {"events": job.events, "done": job.done, "reports": job.reports, "results": job.results,
                           "error": job.error, "saved": job.saved, "saved_paths": sorted(job.saved_paths),
                           "min_confidence": job.min_confidence},
                   "export_job": self.export_job if not self.export_job.get("running") else {}}
            text = json.dumps(doc, default=_plain)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def load_handoff(self) -> bool:
        """Take over the session the previous worker left (then delete the file). True when one was restored."""
        path = self.update.handoff
        if path is None or not path.is_file():
            return False
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            j = doc.get("job") or {}
            job = Job()
            job.events = list(j.get("events") or [])
            job.done = j.get("done")
            job.reports = list(j.get("reports") or [])
            job.results = dict(j.get("results") or {})
            job.error = j.get("error")
            job.saved = bool(j.get("saved"))
            job.saved_paths = set(j.get("saved_paths") or [])
            job.min_confidence = int(j.get("min_confidence") or 65)
            self.job = job
            self.photos = dict(doc.get("photos") or {})
            self.export_job = dict(doc.get("export_job") or {})
            if doc.get("mount"):
                self.mount = doc["mount"]
            return True
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("self-update: could not restore the session: %s", exc)
            self.notes.append("Astro updated itself but could not bring back the last session (loaded photos, "
                              "unsaved results): load the photos again and solve again.")
            return False
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def sky_vocab() -> dict:
        """The sky-label vocabulary (groups, metrics, score) the panel names in Export before any photo is open."""
        from ..annotations import GROUPS, METRICS, SCORE

        return {"groups": GROUPS, "metrics": METRICS, "score": SCORE}

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
        extra = {}
        if b.get("album_id") not in (None, ""):
            extra["album_config"] = self.album_config(b["album_id"])
        return self.listing(photos, scope={"kind": "manual"}, **extra)

    # ------------------------------------------------------ album setup store ---
    @staticmethod
    def _album_key(album_id) -> str:
        if isinstance(album_id, bool) or not str(album_id).isdigit():
            raise ValueError("album_id must be a number")
        return ALBUM_SETUP + str(int(album_id))

    def album_config(self, album_id) -> dict | None:
        """The setup saved for one album (ledger meta table, this Facet library), or None."""
        raw = self.ledger.get_meta(self.ctx.library, self._album_key(album_id))
        try:
            d = json.loads(raw) if raw else None
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def save_album_config(self, album_id, setup: dict) -> bool:
        """Merge ``setup`` into the album's saved setup (None values leave a key as it is); True when it changed."""
        cur = self.album_config(album_id) or {}
        new = {**cur, **{k: v for k, v in setup.items() if v is not None}}
        if new == cur:
            return False
        self.ledger.set_meta(self.ctx.library, self._album_key(album_id), json.dumps(new, sort_keys=True))
        return True

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
        """"Save setup": every folder's ``astro-id.json`` and, with ``album_id``, the album's own setup."""
        from ..session import save_folder_config
        from ..timeutil import format_offset, parse_offset

        paths = list(b.get("paths") or [])
        opts = self.session_opts(b, paths)
        extra: dict = {}
        copies = labeled_copies(b)
        if copies is not None:
            extra["labeled_copies"] = {k: copies.get(k, k == "lines") for k in COPY_KEYS}
            extra["overlay"] = None                  # the older key, replaced by labeled_copies
        view = sky_view(b.get("sky_labels"))
        if view is not None:
            extra["sky_labels"] = view
        cut = _confidence(b.get("min_confidence"))
        if cut is not None:
            extra["min_confidence"] = cut            # step 4 "Save names / Make names searchable at confidence >="
        exp = export_options(b.get("export"))
        if exp is not None:
            extra["export"] = exp                    # Export's options (panel step 6)
        out = {"written": save_folder_config(sorted({str(Path(self.local(p)).parent) for p in paths}), opts, extra),
               "album": None}
        if b.get("album_id") not in (None, ""):
            tol = opts.tolerance_min
            off = None
            if opts.offset is not None:
                off = format_offset(parse_offset(opts.offset)) if str(opts.offset).strip() else ""
            setup = {"clock_offset": off,
                     "time_tolerance_min": (int(tol) if float(tol).is_integer() else tol) if tol is not None else None,
                     "timezone": opts.tz, "solve_source": opts.source if opts.source in ("jpg", "raw", "auto") else None,
                     "labeled_copies": extra.get("labeled_copies"), "sky_labels": extra.get("sky_labels"),
                     "min_confidence": cut, "export": extra.get("export")}
            out["album"] = {"album_id": int(b["album_id"]), "changed": self.save_album_config(b["album_id"], setup)}
        return out

    def solve(self, _q, b):
        paths = list(b.get("paths") or [])
        if not paths:
            raise ValueError("no photos selected")
        opts = self.session_opts(b, paths)
        with self._export_lock:             # one step with api/restart's busy check: never start into a restart
            if self.restarting:
                raise ValueError(UPDATING)
            if self.job.running:
                raise ValueError("a solve is already running")
            self.job = job = Job()
            job.min_confidence = opts.min_confidence
            job.running = True
        threading.Thread(target=self._run, args=(job, paths, opts), name="astro-solve", daemon=True).start()
        return {"started": len(paths), "write": False}

    def _run(self, job: Job, paths: list[str], opts) -> None:
        """Solve only. Nothing goes to Facet here: the user reviews the results, then ``api/save`` writes them."""
        from ..session import run_session

        try:
            back = {self.local(p): p for p in paths}
            done = run_session(list(back), opts, job.emit)
            job.results = {back[lp]: fr for lp, fr in (done.get("results") or {}).items() if lp in back}
            job.done = {k: v for k, v in done.items() if k != "results"}
            job.done["cull"]["frames"] = {back.get(k, k): v for k, v in job.done["cull"]["frames"].items()}
            job.done["quality"] = {fp: fr.get("quality") for fp, fr in job.results.items()}
            job.done["solved_by_path"] = {fp: bool(fr.get("solved")) for fp, fr in job.results.items()}
            job.done["review"] = review_rows(paths, job.results)
            job.done["time_fit"] = time_fit(job.results, opts.offset)
        except Exception as exc:   # surfaced to the page
            log.exception("solve job failed")
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.running = False

    def _ctx_for(self, job: Job) -> SyncContext:
        """The writer with the session's own tag cut (the page's "Min confidence")."""
        from dataclasses import replace

        return replace(self.ctx.refresh(), min_confidence=int(job.min_confidence))

    def job_state(self, q, _b):
        return self.job.snapshot(int((q.get("after") or ["0"])[0]))

    def save(self, _q, b):
        """"Save to Facet": tags + sky labels for the last solve's results (all of them, or ``paths``). Safe to
        repeat: the ledger makes a second save of the same results a no-op."""
        if self.job.running:
            raise ValueError("the solve is still running")
        if not self.job.results:
            raise ValueError("solve first")
        want = b.get("paths")
        results = self.job.results if want is None else {p: r for p, r in self.job.results.items() if p in set(want)}
        if not results:
            raise ValueError("none of those photos are in the last solve")
        cut = _confidence(b.get("min_confidence"))   # step 4 slider (else the solve's)
        if cut is not None:
            self.job.min_confidence = cut
        reports = apply_results(self._ctx_for(self.job), results)
        with self.job.lock:
            self.job.reports = reports
            self.job.saved = True
            self.job.saved_paths |= {r["path"] for r in reports if not r.get("error") and r.get("path")}
        ok = [r for r in reports if not r.get("error")]
        return {"reports": reports, "photos": len(ok), "errors": len(reports) - len(ok),
                "tags_added": sum(len(r.get("add") or []) for r in ok),
                "tags_removed": sum(len(r.get("remove") or []) for r in ok),
                "sky_labels": sum(1 for r in ok if r.get("annotation") in ("stored", "unchanged")),
                "annotations": self.ctx.mode, "names_as": "searchable" if self.ctx.model_d else "tags",
                # model D: names Facet made searchable (stored now) or that were already (unchanged payloads)
                "searchable": sum(r.get("searchable", len(r.get("names") or [])
                                        if r.get("annotation") in ("stored", "unchanged") else 0) for r in ok),
                "old_tags_removed": sum(len(r.get("migrated") or []) for r in ok),
                # model D: per-name tags written through the user path stay (Facet only lets an add-on remove its own)
                "old_tags_yours": sum(len(r.get("legacy") or []) for r in ok)}

    def annotations(self, q, _b):
        """The astro-id sky labels stored on one photo (the photo open in Facet), for the sky-label controls:
        the payload's vocabulary (groups, metrics, score), not its geometry."""
        path = (q.get("path") or [""])[0]
        if not path:
            raise ValueError("path required")
        feats = self.client.features()
        if not feats.get("annotations_read"):
            return {"path": path, "payload": None, "note": "this Facet has no annotations route"}
        if feats.get("annotation_summary"):
            # model D: get_annotations summary:true (no geometry, always under Facet's tool-result cap)
            try:
                rows = self.client.annotations_summary(path)
            except FacetError as exc:
                if exc.status == 404:
                    return {"path": path, "payload": None}
                raise
            mine = next((r for r in rows if r.get("source") in (ANNOTATION_SOURCE, self.addon_id)), None)
            if mine is None:
                return {"path": path, "payload": None}
            groups = [{k: g.get(k) for k in ("id", "label", "default") if k in g} for g in mine.get("groups") or []]
            return {"path": path, "payload": {"version": mine.get("version"), "groups": groups,
                                              "metrics": mine.get("metrics"), "score": mine.get("score")},
                    "items": mine.get("items") or 0, "searchable": mine.get("indexed")}
        try:
            rows = self.client.annotations(path)
        except FacetError as exc:
            if exc.status == 404:
                return {"path": path, "payload": None}
            raise
        mine = next((r.get("payload") for r in rows if r.get("source") in (ANNOTATION_SOURCE, self.addon_id)
                     and isinstance(r.get("payload"), dict)), None)
        if mine is None:
            return {"path": path, "payload": None}
        return {"path": path, "payload": {k: mine.get(k) for k in ("version", "groups", "metrics", "score")},
                "items": len(mine.get("items") or [])}

    # ------------------------------------------------------------ count / export ---
    def count(self, _q, b):
        """How many photos Facet has under a folder (``path_prefix``) or in an album, and whether that folder or
        album has a saved setup: the label of the panel's "Load what Facet shows" button."""
        if b.get("album_id") not in (None, ""):
            self._album_key(b["album_id"])            # a number, or ValueError
            aid = int(b["album_id"])
            a = next((a for a in self.client.albums() if a.get("id") is not None and int(a["id"]) == aid), None)
            if a is None:
                raise ValueError("no such album")
            out = {"count": a.get("photo_count"), "name": a.get("name"), "smart": bool(a.get("is_smart")),
                   "setup": self.album_config(aid) is not None}
            path = b.get("path")
            if isinstance(path, str) and path and not out["smart"]:
                # a photo opened from an album: Facet's context names the photo, not the album; is it in this one?
                members = self.client.list_photos(album_id=aid, limit=MAX_SCOPE_PHOTOS)
                out["contains"] = any(p.get("path") == path for p in members)
            return out
        prefix = b.get("path_prefix")
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("path_prefix or album_id required")
        photos = self.client.list_photos(path_prefix=prefix, limit=MAX_SCOPE_PHOTOS)
        setup = bool(photos) and bool(self.folder_config(photos[0]["path"]).get("file"))
        return {"count": len(photos), "setup": setup}

    def _export_source(self, path: str):
        """(payload, local image) for one photo: the last solve's result when it has the photo, else the sky labels
        stored in Facet. Raises ValueError with a plain reason when there is nothing to draw."""
        fr = self.job.results.get(path) if not self.job.running else None
        if isinstance(fr, dict) and fr.get("solved") and isinstance(fr.get("annotations"), dict):
            return fr["annotations"], fr.get("file") or self.local(path)
        if not self.client.features().get("annotations_read"):
            raise ValueError("no sky labels: plate solve it in this session first")
        try:
            rows = self.client.annotations(path)
        except FacetError as exc:
            if exc.status != 404:
                raise
            rows = []
        mine = next((r.get("payload") for r in rows if r.get("source") in (ANNOTATION_SOURCE, self.addon_id)
                     and isinstance(r.get("payload"), dict)), None)
        if not mine or not mine.get("items"):
            raise ValueError("no sky labels: plate solve it, then Save to Facet")
        return mine, self.local(path)

    def export(self, _q, b):
        """Export: burn what the sky-label ``view`` shows into JPG copies in "<folder>-annotated", from the stored
        results (no re-solve). ``{paths, view, options: {lines, scores, legend}}``; runs in the background, progress
        on ``GET api/export``."""
        paths = [p for p in (b.get("paths") or []) if isinstance(p, str) and p]
        if not paths:
            raise ValueError("no photos to export")
        with self._export_lock:             # a double click must not start two writers on the same files
            if self.restarting:
                raise ValueError(UPDATING)
            if self.export_job.get("running"):
                raise ValueError("an export is already running")
            if self.job.running:
                raise ValueError("the solve is still running")
            o = export_options(b.get("options")) or {}
            opts = {"lines": o.get("lines", True), "scores": o.get("scores", False), "legend": o.get("legend", True)}
            view = b.get("view") if isinstance(b.get("view"), dict) else {}
            job = {"running": True, "total": len(paths), "done": 0, "current": None, "written": [], "errors": [],
                   "folders": []}
            self.export_job = job
            threading.Thread(target=self._export_run, args=(job, paths, view, opts), name="astro-export",
                             daemon=True).start()
        return {"started": len(paths)}

    @staticmethod
    def _export_name(src: Path) -> str:
        """``<stem>_astro.jpg``; ``<stem>_<ext>_astro.jpg`` when another file with the same stem sits in the folder
        (IMG_1.CR2 + IMG_1.JPG), so a RAW and its JPG never write the same copy."""
        try:
            from ..imageio import DIRECT_EXT, RAW_EXT

            images = DIRECT_EXT | RAW_EXT          # an .xmp sidecar is not a twin
            twin = any(f.stem == src.stem and f.suffix.lower() != src.suffix.lower() and f.suffix.lower() in images
                       for f in src.parent.iterdir())
        except OSError:
            twin = False
        return f"{src.stem}_{src.suffix.lstrip('.').lower()}_astro.jpg" if twin else f"{src.stem}_astro.jpg"

    def _export_run(self, job: dict, paths: list[str], view: dict, opts: dict) -> None:
        from ..export import render
        from ..pipeline import default_overlay_dir

        try:
            for p in paths:
                job["current"] = _base(p)
                try:
                    payload, src = self._export_source(p)
                    src = Path(src)
                    od = default_overlay_dir(src.parent)
                    r = render(src, od / self._export_name(src), payload, view, **opts)
                    job["written"].append({"path": p, "out": r["out"], "items": r["items"]})
                    f = str(od).replace(chr(92), "/")
                    if f not in job["folders"]:
                        job["folders"].append(f)
                except Exception as exc:   # one photo never stops the export
                    job["errors"].append({"path": p, "error": str(exc) if isinstance(exc, ValueError)
                                          else f"{type(exc).__name__}: {exc}"})
                job["done"] += 1
        finally:
            job["current"] = None
            job["running"] = False

    def export_state(self, _q, _b):
        return dict(self.export_job) if self.export_job else {"running": False, "total": 0, "done": 0}

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
        if not self.job.saved_paths:
            raise ValueError("Save to Facet first (step 4): the astro-review status tag only goes on photos already saved")
        # Only the photos already saved: re-asserting their tags + sky labels writes nothing new for them.
        results = {p: r for p, r in self.job.results.items() if p in self.job.saved_paths}
        flagged = set(b.get("paths") or [])
        extra = {p: ([TAG_REVIEW] if p in flagged else []) for p in results}
        reports = apply_results(self._ctx_for(self.job), results, extra_tags=extra)
        return {"reports": reports, "skipped_unsaved": len(flagged - set(results))}

    ROUTES = {("GET", "api/state"): "state", ("GET", "api/albums"): "albums", ("GET", "api/job"): "job_state",
              ("POST", "api/photos"): "list_photos", ("POST", "api/scope"): "scope", ("POST", "api/location"): "location",
              ("POST", "api/preview"): "preview", ("POST", "api/save-config"): "save_config",
              ("POST", "api/solve"): "solve", ("POST", "api/recull"): "recull",
              ("POST", "api/review-tags"): "review_tags", ("POST", "api/save"): "save",
              ("GET", "api/annotations"): "annotations", ("POST", "api/count"): "count",
              ("POST", "api/export"): "export", ("GET", "api/export"): "export_state",
              ("POST", "api/restart"): "restart"}
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


def build_addon(args, env: dict | None = None, client: FacetClient | None = None, ledger: Ledger | None = None,
                sock=None):
    """Add-on mode, step 1: bind the socket, or serve on ``sock`` (a keeper's, keeper.py); nothing answers until
    ``finish_addon``. Returns (server, box, aenv)."""
    if getattr(args, "legacy_db", None):
        raise ValueError("--legacy-db is not available in add-on mode (annotations go through Facet's API or are "
                         "skipped)")
    aenv = AddonEnv(env)
    if aenv.addon_id != ADDON_ID:
        log.warning("Facet started this add-on as %r; its manifest id is %r", aenv.addon_id, ADDON_ID)
    box: dict = {"client": client, "ledger": ledger}
    handler = make_addon_handler(box, aenv.addon_id, aenv.secret)
    if sock is None:
        server = ThreadingHTTPServer(("127.0.0.1", aenv.port), handler)
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
        server.server_address = sock.getsockname()
    server.daemon_threads = True
    return server, box, aenv


def finish_addon(args, box: dict, aenv: AddonEnv, update: SelfUpdate | None = None) -> App:
    """Add-on mode, step 2 (after the port line): connect to Facet and build the session app. ``update``: a
    keeper's worker (it can restart itself; it takes over the session its predecessor left)."""
    client = box.get("client") or connect(aenv.url, **aenv.credentials)
    ledger = box.get("ledger") or Ledger(getattr(args, "ledger", None))
    ctx = SyncContext(client=client, ledger=ledger, addon=True,
                      annotations=not getattr(args, "no_annotations", False)).prepare()
    app = App(client, ledger, ctx, parse_path_map(getattr(args, "path_map", None)), addon_id=aenv.addon_id)
    if aenv.credential_note:
        app.notes.append(aenv.credential_note)
        log.warning("%s", aenv.credential_note)
    if update is not None:
        app.update = update
        if app.load_handoff():
            log.info("self-update: session restored (%d photos, %d results)", len(app.photos), len(app.job.results))
    box["app"] = app
    return app


def serve_addon(args, env: dict | None = None, out=None, lifeline: bool = True, stdin=None) -> int:
    """The add-on server. Run by the keeper as its worker (keeper.py: the keeper's socket, no port line, exits with
    ``keeper.RESTART_EXIT`` when asked to update), or on its own (ASTRO_ID_NO_KEEPER=1, tests)."""
    e = os.environ if env is None else env
    out = out or sys.stdout
    sock = keeper.inherited_socket(e, stdin)
    server, box, aenv = build_addon(args, env, sock=sock)
    update = None
    if sock is None:
        out.write(f"FACET_ADDON_PORT={server.server_address[1]}\n")
        out.flush()
    else:
        update = SelfUpdate(True, keeper.handoff_path(e))
    if lifeline:
        from .lifeline import watch

        log.info("lifeline: exits when any of %s ends", watch() or "nothing (not watched)")
    app = None
    code = 0
    if update is not None:
        def stop_for_update():
            nonlocal code
            code = keeper.RESTART_EXIT
            server.shutdown()
        update.exit = stop_for_update
    try:
        app = finish_addon(args, box, aenv, update)
        log.info("astro-id add-on %s on 127.0.0.1:%d for Facet %s (version %s, page %s); annotations: %s",
                 aenv.addon_id, server.server_address[1], app.client.url, app.ctx.version, PAGE_VERSION,
                 app.ctx.mode or "skipped")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if app is not None:
            app.ctx.close()
            app.ledger.close()
    return code


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
