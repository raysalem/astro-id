"""astro-id: Facet plugin that plate-solves astro photos and writes object tags.

Drop-in Facet plugin (copy or junction this file into ``<facet>/plugins/``).
Stdlib only; Facet's own modules (``db.manual_tags``, ``config_resolve``) are
imported lazily inside functions.

Event hook: ``on_score_complete(data)`` (single-pass scans). Facet's multi-pass scan never emits
it, so when this module loads inside a ``facet.py`` process it also registers an exit hook that
spawns a detached catch-up run (``--pending``) for astro photos that have no astro-id tags yet.

Backfill CLI::

    python plugins/astro_id.py <folder-or-file> [--min-confidence N] [--db PATH] [--dry-run]
    python plugins/astro_id.py --pending [--since "YYYY-MM-DD HH:MM:SS"] [--db PATH]

Tags are written to Facet's ``photo_manual_tags`` side table. The table's
``source`` column only allows 'user' or 'xmp', so rows owned by this plugin are
``source='user', created_by='astro-id'``; only rows with that ``created_by`` are
ever replaced or deleted. See ``facet_plugin/README.md``.

Config (``plugins.astro_id`` in the resolved scoring config)::

    "plugins": {"enabled": true, "astro_id": {
        "engine": "C:/Users/raysa/astro-id",
        "uv": "C:/Users/raysa/.local/bin/uv.exe",
        "min_confidence": 65, "roots": ["//192.168.1.90/Photos and Videos/2026"],
        "overlay_dir": null, "timeout_s": 600, "enabled": true}}
"""

from __future__ import annotations

import argparse
import atexit
import datetime as _dt
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

logger = logging.getLogger("facet.plugins.astro_id")

CONTRACT_VERSION = 1
OWNER = "astro-id"            # created_by marker on rows this plugin owns
SOURCE = "user"               # photo_manual_tags.source (CHECK allows 'user'/'xmp' only)
TAG_SOLVED = "plate-solved"
TAG_FAILED = "plate-solve failed"
ASTRO_TAGS = frozenset({"astrophotography", "night sky", "stars", "milky way",
                        "aurora", "nebula"})
DEFAULT_UV = r"C:\Users\raysa\.local\bin\uv.exe"
DEFAULTS = {
    "enabled": True,
    "engine": None,
    "uv": DEFAULT_UV,
    "min_confidence": 65,
    "roots": [],
    "overlay_dir": None,
    "timeout_s": 600,
    "settle_s": 3.0,       # wait so a scan batch can accumulate before a job starts
    "batch_min": 4,        # this many pending frames in one folder -> one folder run
    "db_path": None,
    # Tag policy, passed to the engine when set (None = astro-id.json tag_policy, else engine defaults:
    # levels 1-4, stars off, max_catalog_mag 6.0, tag_style plain).
    "tag_levels": None,        # e.g. [1, 2, 3, 4] or "1,2,3,4,5"
    "star_mag_limit": None,    # e.g. 1.5 -> the ~20 brightest named stars become tags
    "max_catalog_mag": None,   # faintest Messier/NGC/IC object to tag (engine default 6.0)
    "tag_style": None,         # "plain" | "kind"
    # Stacking sequences (folder runs): None = astro-id.json, else engine defaults (representative, 120 s).
    "stack_mode": None,        # "representative" | "all"
    "stack_gap_s": None,
    # Catch-up after a scan: a facet.py process spawns a detached --pending run at exit.
    "catch_up": True,
    "log_path": None,          # default %LOCALAPPDATA%/Facet/data/logs/astro-id.log
    # "time_tolerance_min": 15 -- when set, passed as --time-tolerance (else astro-id.json / engine default)
    # "timezone": "America/Los_Angeles" -- when set, passed as --timezone with a naive Facet date
}

_cfg_override: dict | None = None
_db_override: str | None = None


class EngineError(Exception):
    """The engine failed as a whole (no per-frame results are usable)."""


# ----------------------------------------------------------------- config ---

def configure(cfg: dict | None = None, db_path: str | None = None) -> None:
    """Set config / DB path explicitly (CLI and tests); None clears the override."""
    global _cfg_override, _db_override
    _cfg_override = cfg
    _db_override = db_path


def get_config() -> dict:
    """Plugin config: DEFAULTS < resolved scoring config ``plugins.astro_id``."""
    cfg = dict(DEFAULTS)
    if _cfg_override is not None:
        cfg.update(_cfg_override)
        return cfg
    try:
        from config_resolve import load_resolved
        block = (load_resolved().get("plugins") or {}).get("astro_id") or {}
        cfg.update(block)
    except Exception:
        logger.exception("astro_id: could not read the scoring config")
    return cfg


def get_db_path(cfg: dict | None = None) -> str:
    """Override > plugin ``db_path`` > ``--db`` in argv (scan) > $DB_PATH > Facet default."""
    if _db_override:
        return _db_override
    if cfg and cfg.get("db_path"):
        return cfg["db_path"]
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--db" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--db="):
            return a[5:]
    return os.environ.get("DB_PATH") or "photo_scores_pro.db"


# ------------------------------------------------------------------ paths ---

def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).replace("\\", "/")


def _stem_key(p: str) -> tuple[str, str]:
    d, f = os.path.split(_norm(p))
    return d, os.path.splitext(f)[0]


def _under(path: str, root: str) -> bool:
    r = _norm(root).rstrip("/")
    n = _norm(path)
    return n == r or n.startswith(r + "/")


def is_astro(data: dict, cfg: dict) -> bool:
    """category 'astro', astro-ish tags, or a path under a configured root."""
    if str(data.get("category") or "").lower() == "astro":
        return True
    tags = {t.strip().lower() for t in str(data.get("tags") or "").split(",") if t.strip()}
    if tags & ASTRO_TAGS:
        return True
    path = data.get("path") or ""
    return any(_under(path, r) for r in (cfg.get("roots") or []))


def map_results(requested: list[str], results: list[dict]) -> dict[str, dict]:
    """{requested photo path: engine result}. Exact path first, then same-stem pair
    (a RAW event path maps to the engine's JPG result)."""
    by_norm = {_norm(r.get("file", "")): r for r in results if r.get("file")}
    by_stem: dict = {}
    for r in results:
        if r.get("file"):
            by_stem.setdefault(_stem_key(r["file"]), r)
    out = {}
    for p in requested:
        r = by_norm.get(_norm(p)) or by_stem.get(_stem_key(p))
        if r is not None:
            out[p] = r
    return out


# ----------------------------------------------------------------- engine ---

def _run_engine(target: str, min_confidence: int, cfg: dict, extra: list[str] | None = None) -> list[dict]:
    """Run ``astro-id solve <target> --json``; return the result frames.

    Raises EngineError on failure, timeout, bad JSON or contract != 1."""
    engine = cfg.get("engine")
    if not engine:
        raise EngineError("plugins.astro_id.engine is not configured")
    cmd = [cfg.get("uv") or DEFAULT_UV, "run", "--project", str(engine), "astro-id",
           "solve", target, "--json", "--min-confidence", str(int(min_confidence))]
    cmd += list(extra or [])
    overlay = cfg.get("overlay_dir")
    if overlay:
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        cmd += ["--overlay-dir", str(overlay).replace("{folder}", folder)]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              timeout=float(cfg.get("timeout_s") or 600), **kwargs)
    except subprocess.TimeoutExpired:
        raise EngineError(f"engine timed out on {target}") from None
    except OSError as ex:
        raise EngineError(f"cannot start engine: {ex}") from None
    if proc.returncode != 0:
        raise EngineError(f"engine exit {proc.returncode}: {(proc.stderr or '')[-400:]}")
    return parse_engine_output(proc.stdout)


def parse_engine_output(text: str) -> list[dict]:
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as ex:
        raise EngineError(f"engine output is not JSON: {ex}") from None
    if not isinstance(doc, dict) or doc.get("contract") != CONTRACT_VERSION:
        raise EngineError(f"unsupported engine contract {doc.get('contract') if isinstance(doc, dict) else doc!r}"
                          f" (need {CONTRACT_VERSION})")
    results = doc.get("results")
    if not isinstance(results, list):
        raise EngineError("engine output has no results list")
    return results


# ------------------------------------------------------------------- tags ---

def _level(o: dict) -> int:
    lv = o.get("level")
    return int(lv) if isinstance(lv, (int, float)) else 9


def desired_tags(result: dict, min_confidence: int) -> list[str]:
    """Normalized tags for one frame in cap order: status tag, then level 1..5
    (constellation, Sun/Moon/planet, catalog object, asterism, star), confidence desc within a level.
    Only objects the engine marked ``taggable`` (absent = taggable, pre-hierarchy engines) and at
    least ``min_confidence``."""
    from db.manual_tags import ManualTagError, normalize_manual_tag
    if not result.get("solved"):
        return [TAG_FAILED]
    tags = [TAG_SOLVED]
    objs = sorted((o for o in result.get("objects") or []
                   if isinstance(o.get("confidence"), (int, float))
                   and o["confidence"] >= min_confidence and o.get("taggable", True) is not False),
                  key=lambda o: (_level(o), -o["confidence"], str(o.get("tag"))))
    for o in objs:
        try:
            t = normalize_manual_tag(o.get("tag"))
        except ManualTagError:
            logger.warning("astro_id: skipping unusable tag %r", o.get("tag"))
            continue
        if t not in tags:
            tags.append(t)
    return tags


def current_tags(conn, photo_path: str) -> list[str]:
    return sorted(r[0] for r in conn.execute(
        "SELECT tag FROM photo_manual_tags WHERE photo_path = ? AND created_by = ?",
        (photo_path, OWNER)))


def plan_tags(conn, photo_path: str, tags: list[str]) -> list[str]:
    # ``tags`` arrives in priority order (desired_tags), so the cap drops the lowest priority first.
    """The subset of ``tags`` that will actually be stored (cap, dedup, AI-tag skip)."""
    from db.manual_tags import MAX_TAGS_PER_PHOTO, is_ai_tag
    row = conn.execute("SELECT tags FROM photos WHERE path = ?", (photo_path,)).fetchone()
    ai = row[0] if row else None
    foreign = {r[0] for r in conn.execute(
        "SELECT tag FROM photo_manual_tags WHERE photo_path = ? "
        "AND (created_by IS NULL OR created_by != ?)", (photo_path, OWNER))}
    room = MAX_TAGS_PER_PHOTO - len(foreign)
    keep = []
    for t in tags:                       # already ordered by priority
        if t in foreign or is_ai_tag(ai, t):
            continue
        if len(keep) >= room:
            logger.warning("astro_id: %s at the %d-tag cap, dropped %r",
                           photo_path, MAX_TAGS_PER_PHOTO, t)
            continue
        keep.append(t)
    return keep


def write_tags(conn, photo_path: str, tags: list[str]) -> tuple[list[str], list[str]]:
    """Replace this plugin's rows for one photo in ONE transaction.

    Returns (added, removed). Other sources' rows are never touched."""
    from db.manual_tags import insert_manual_tag
    keep = plan_tags(conn, photo_path, tags)
    before = set(current_tags(conn, photo_path))
    prev_iso = conn.isolation_level
    conn.isolation_level = None          # explicit transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM photo_manual_tags WHERE photo_path = ? AND created_by = ?",
                         (photo_path, OWNER))
            for t in keep:
                insert_manual_tag(conn, photo_path, t, SOURCE, OWNER)
            # Same cache invalidation the manual-tag API does (tag dropdown row).
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'stats_cache'").fetchone():
                conn.execute("DELETE FROM stats_cache WHERE key = 'tags'")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = prev_iso
    after = set(keep)
    return sorted(after - before), sorted(before - after)


def facet_hints(conn, photo_path: str) -> dict:
    """What Facet knows about one photo, as engine flags.

    SITE: Facet GPS (manual or EXIF) > astro-id.json (engine) > nothing.
    TIME: manual Facet date (date_manually_set=1) > astro-id.json offset on EXIF (engine) > raw EXIF.
    A non-manual date_taken is the camera's (wrong) clock, so it is never passed.
    date_taken is a naive camera-local walltime ('YYYY:MM:DD HH:MM:SS', see utils/capture_dates.py);
    the engine reads it in the folder's timezone (astro-id.json, else inferred from the site)."""
    row = conn.execute("SELECT gps_latitude, gps_longitude, date_taken, date_manually_set "
                       "FROM photos WHERE path = ?", (photo_path,)).fetchone()
    hints: dict = {"site": None, "time": None}
    if not row:
        return hints
    lat, lon, taken, manual = row
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float))             and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
        hints["site"] = f"{lat:.6f},{lon:.6f}"
    if manual:
        try:
            from utils.capture_dates import parse_capture_date
            dt = parse_capture_date(taken)
        except Exception:
            dt = None
        if dt is not None:
            hints["time"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return hints


def policy_flags(cfg: dict) -> list[str]:
    """Tag-policy and stacking flags from the plugin config (only the keys that are set)."""
    flags: list[str] = []
    lv = cfg.get("tag_levels")
    if lv is not None:
        flags += ["--tag-levels", ",".join(str(int(x)) for x in lv) if isinstance(lv, (list, tuple)) else str(lv)]
    for key, flag in (("star_mag_limit", "--star-mag-limit"), ("max_catalog_mag", "--max-catalog-mag"),
                      ("tag_style", "--tag-style"), ("stack_mode", "--stack-mode"), ("stack_gap_s", "--stack-gap")):
        if cfg.get(key) is not None:
            flags += [flag, str(cfg[key])]
    return flags


def engine_flags(hints: dict, cfg: dict) -> list[str]:
    flags: list[str] = policy_flags(cfg)
    if hints.get("site"):
        flags += ["--site", hints["site"]]
    if hints.get("time"):
        flags += ["--time", hints["time"]]
        if cfg.get("timezone"):
            flags += ["--timezone", str(cfg["timezone"])]
    if cfg.get("time_tolerance_min") is not None:
        flags += ["--time-tolerance", str(cfg["time_tolerance_min"])]
    return flags


def _open_db(db_path: str):
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def process_paths(paths: list[str], cfg: dict, db_path: str, min_confidence: int | None = None,
                  dry_run: bool = False) -> dict[str, tuple[list[str], list[str]]]:
    """Solve ``paths`` (Facet photo paths) and write tags. Returns {path: (added, removed)}.

    Groups by folder; a folder with >= batch_min frames is solved as one folder run."""
    mc = int(cfg.get("min_confidence", 65) if min_confidence is None else min_confidence)
    changes: dict = {}
    conn = None
    try:
        conn = _open_db(db_path)
        hints = {}
        for p in paths:
            try:
                hints[p] = facet_hints(conn, p)
            except Exception:
                logger.exception("astro_id: cannot read Facet location/time for %s", p)
                hints[p] = {"site": None, "time": None}
        # Group by (folder, site): a folder run applies one --site to every frame, and --time is
        # per file, so only photos sharing folder+site and with no own --time can batch. Else per file.
        groups: dict = {}
        for p in paths:
            groups.setdefault((os.path.dirname(p), hints[p]["site"]), []).append(p)
        jobs = []
        for (folder, site), group in groups.items():
            plain = [p for p in group if not hints[p]["time"]]
            if len(plain) >= int(cfg.get("batch_min", 4)):
                jobs.append((folder, plain, engine_flags({"site": site}, cfg)))
                solo = [p for p in group if hints[p]["time"]]
            else:
                solo = group
            jobs += [(p, [p], engine_flags(hints[p], cfg)) for p in solo]
        for target, reqs, flags in jobs:
            try:
                results = _run_engine(target, mc, cfg, flags)
            except EngineError as ex:
                logger.error("astro_id: %s", ex)
                continue
            mapped = map_results(reqs, results)
            for p in reqs:
                r = mapped.get(p)
                if r is None:
                    logger.warning("astro_id: no engine result for %s", p)
                    continue
                try:
                    if not conn.execute("SELECT 1 FROM photos WHERE path = ?", (p,)).fetchone():
                        logger.warning("astro_id: %s is not in the Facet DB", p)
                        continue
                    want = desired_tags(r, mc)
                    if dry_run:
                        keep = set(plan_tags(conn, p, want))
                        have = set(current_tags(conn, p))
                        changes[p] = (sorted(keep - have), sorted(have - keep))
                    else:
                        changes[p] = write_tags(conn, p, want)
                except Exception:
                    logger.exception("astro_id: failed writing tags for %s", p)
    except Exception:
        logger.exception("astro_id: cannot open the Facet database %s", db_path)
    finally:
        if conn is not None:
            conn.close()
    return changes


# ----------------------------------------------------------------- worker ---

class _Worker:
    """One background thread, one engine job at a time, de-duplicated queue."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, None] = {}     # ordered set of paths
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, path: str) -> bool:
        with self._lock:
            if path in self._pending:
                return False
            self._pending[path] = None
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="astro-id-worker",
                                                daemon=True)
                self._thread.start()
        self._wake.set()
        return True

    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def _take(self) -> list[str]:
        with self._lock:
            items = list(self._pending)
            self._pending.clear()
            return items

    def _run(self):
        while True:
            self._wake.wait(timeout=30)
            self._wake.clear()
            cfg = get_config()
            time.sleep(float(cfg.get("settle_s") or 0))
            batch = self._take()
            if not batch:
                with self._lock:
                    if not self._pending:
                        self._thread = None
                        return
                continue
            try:
                ch = process_paths(batch, cfg, get_db_path(cfg))
                logger.info("astro_id: tagged %d photo(s)", len(ch))
            except Exception:
                logger.exception("astro_id: job failed")


_worker = _Worker()
_warned_engine = False


def on_score_complete(data: dict) -> None:
    """Facet hook: queue astro photos for plate-solving. Never raises, never blocks."""
    global _warned_engine
    try:
        cfg = get_config()
        if not cfg.get("enabled", True) or not isinstance(data, dict):
            return
        path = data.get("path")
        if not path or not is_astro(data, cfg):
            return
        if not cfg.get("engine"):
            if not _warned_engine:
                _warned_engine = True
                logger.warning("astro_id: plugins.astro_id.engine is not set; skipping")
            return
        _worker.submit(path)
    except Exception:
        logger.exception("astro_id: on_score_complete failed")


# ---------------------------------------------------------------- pending ---

def _utc_now() -> str:
    """UTC 'YYYY-MM-DD HH:MM:SS', the format of Facet's scanned_at and created_at (SQLite datetime('now'))."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def select_pending(db_path: str, cfg: dict, since: str | None = None) -> list[str]:
    """Photos that need an astro-id run: no astro-id rows yet, or rescanned (scanned_at) after them.

    Candidates: every photo under ``roots``; other astro photos (category/tags) only when scanned at or
    after ``since`` (None = all), so a catch-up never sweeps the whole historical library."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            "SELECT p.path, p.category, p.tags, p.scanned_at, "
            "(SELECT MAX(t.created_at) FROM photo_manual_tags t WHERE t.photo_path = p.path AND t.created_by = ?) "
            "FROM photos p", (OWNER,)).fetchall()
    finally:
        conn.close()
    out = []
    roots = cfg.get("roots") or []
    for path, cat, tags, scanned, tagged in rows:
        under = any(_under(path, r) for r in roots)
        if not under:
            if not is_astro({"path": path, "category": cat, "tags": tags}, {"roots": []}):
                continue
            if since and not (scanned and scanned >= since):
                continue
        if tagged is None or (scanned and scanned > tagged):
            out.append(path)
    return sorted(out)


def default_log_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Facet", "data", "logs", "astro-id.log")


def _log_path(cfg: dict) -> str:
    return cfg.get("log_path") or default_log_path()


def _lock_path(cfg: dict) -> str:
    return os.path.join(os.path.dirname(_log_path(cfg)), "astro-id-catchup.lock")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_held(path: str) -> bool:
    """True when a live catch-up process owns the lock file."""
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int((fh.read().split() or ["0"])[0])
    except (OSError, ValueError):
        return False
    return pid != os.getpid() and _pid_alive(pid)


def acquire_lock(path: str) -> bool:
    """Create the lock file with this pid; a lock left by a dead process is taken over."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_held(path):
                return False
            try:
                os.remove(path)          # stale
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} {_utc_now()}\n")
        return True
    return False


def release_lock(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            if int((fh.read().split() or ["0"])[0]) != os.getpid():
                return
        os.remove(path)
    except (OSError, ValueError):
        pass


def run_pending(cfg: dict, db_path: str, since: str | None = None, min_confidence: int | None = None,
                dry_run: bool = False, max_rounds: int = 3) -> dict:
    """Process every pending photo; re-check up to ``max_rounds`` times (a scan may add more meanwhile).

    Holds the catch-up lock; returns {} without work when another catch-up is running."""
    lock = _lock_path(cfg)
    if not acquire_lock(lock):
        logger.info("astro_id: another catch-up is running (%s); exiting", lock)
        return {}
    changes: dict = {}
    tried: set = set()
    try:
        for _ in range(max_rounds):
            todo = [p for p in select_pending(db_path, cfg, since) if p not in tried]
            if not todo:
                break
            logger.info("astro_id: catch-up: %d pending photo(s)", len(todo))
            tried.update(todo)
            changes.update(process_paths(todo, cfg, db_path, min_confidence, dry_run))
            if dry_run:
                break
    finally:
        release_lock(lock)
    return changes


# -------------------------------------------------------------- exit hook ---

_PROCESS_START = _utc_now()
_exit_hook_registered = False


def _is_scan_process(argv: list[str] | None = None) -> bool:
    """Only Facet's scanner (``facet.py``) gets the exit hook; the viewer and API never do."""
    argv = sys.argv if argv is None else argv
    if not argv:
        return False
    return os.path.basename(str(argv[0])).lower() in ("facet.py", "facet")


def _argv_value(flag: str) -> str | None:
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a[len(flag) + 1:]
    return None


def spawn_catch_up(cfg: dict, db_path: str, since: str | None) -> subprocess.Popen | None:
    """Start ``<facet python> plugins/astro_id.py --pending`` detached; output appended to the log."""
    log_path = _log_path(cfg)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    script = os.path.abspath(__file__)
    facet_dir = os.path.dirname(os.path.dirname(script))
    cmd = [sys.executable, script, "--pending", "--db", os.path.abspath(db_path)]
    if since:
        cmd += ["--since", since]
    config = _argv_value("--config")
    if config:
        cmd += ["--config", os.path.abspath(config)]
    env = dict(os.environ)
    env["DB_PATH"] = os.path.abspath(db_path)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    kwargs: dict = {}
    if os.name == "nt":
        # CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, survives the scan's exit
        kwargs["creationflags"] = 0x08000000 | 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n[{_utc_now()} UTC] spawn catch-up from pid {os.getpid()}: {' '.join(cmd)}\n")
        log.flush()
        return subprocess.Popen(cmd, cwd=facet_dir, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, close_fds=True, **kwargs)


def _on_exit() -> None:
    """Scan exit: spawn a detached catch-up when astro photos still lack astro-id tags. Never raises."""
    try:
        cfg = get_config()
        if not cfg.get("enabled", True) or not cfg.get("catch_up", True) or not cfg.get("engine"):
            return
        db_path = get_db_path(cfg)
        if not os.path.exists(db_path):
            return
        pending = select_pending(db_path, cfg, _PROCESS_START)
        if not pending:
            return
        if lock_held(_lock_path(cfg)):
            logger.info("astro_id: %d photo(s) pending; a catch-up is already running", len(pending))
            return
        proc = spawn_catch_up(cfg, db_path, _PROCESS_START)
        logger.info("astro_id: %d photo(s) pending; catch-up started (pid %s), log %s",
                    len(pending), getattr(proc, "pid", "?"), _log_path(cfg))
    except Exception:
        logger.exception("astro_id: exit hook failed")


def _register_exit_hook() -> bool:
    global _exit_hook_registered
    if _exit_hook_registered or not _is_scan_process():
        return False
    atexit.register(_on_exit)
    _exit_hook_registered = True
    return True


# --------------------------------------------------------------- backfill ---

def _select_photos(db_path: str, target: str) -> list[str]:
    conn = _open_db(db_path)
    try:
        rows = [r[0] for r in conn.execute("SELECT path FROM photos")]
    finally:
        conn.close()
    t = os.path.abspath(target) if not target.startswith(("\\\\", "//")) else target
    return sorted(p for p in rows if _under(p, t))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Backfill astro-id tags for photos already in Facet.")
    ap.add_argument("target", nargs="?", help="folder or file under which photos are selected")
    ap.add_argument("--pending", action="store_true",
                    help="process astro photos without astro-id tags (or rescanned since), under a lock")
    ap.add_argument("--since", default=None,
                    help="with --pending: outside roots, only photos scanned at/after this UTC time")
    ap.add_argument("--min-confidence", type=int, default=None)
    ap.add_argument("--db", default=None, help="Facet database (default $DB_PATH)")
    ap.add_argument("--config", default=None, help="scoring config (default $FACET_CONFIG)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if a.config:
        os.environ["FACET_CONFIG"] = a.config
    cfg = get_config()
    db_path = a.db or get_db_path(cfg)
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if a.pending:
        logger.info("astro_id: catch-up start (pid %d, since %s, db %s)", os.getpid(), a.since, db_path)
        changes = run_pending(cfg, db_path, a.since, a.min_confidence, a.dry_run)
        for p, (add, rem) in sorted(changes.items()):
            print(f"{p}\n   +{add}\n   -{rem}")
        logger.info("astro_id: catch-up done, %d photo(s) tagged", len(changes))
        return 0
    if not a.target:
        ap.error("a target folder/file or --pending is required")
    paths = _select_photos(db_path, a.target)
    if not paths:
        print("No photos under that path in the Facet database.")
        return 0
    print(f"{len(paths)} photo(s) selected; DB {db_path}" + ("  [dry run]" if a.dry_run else ""))
    changes = process_paths(paths, cfg, db_path, a.min_confidence, a.dry_run)
    for p in paths:
        if p in changes:
            add, rem = changes[p]
            print(f"{p}\n   +{add}\n   -{rem}")
        else:
            print(f"{p}\n   (no result)")
    return 0


_register_exit_hook()


if __name__ == "__main__":
    # Run from plugins/: make the Facet checkout (parent of plugins/) importable.
    _facet = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _p in (_facet, os.getcwd()):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    sys.exit(main())
