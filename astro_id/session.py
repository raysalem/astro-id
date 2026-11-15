"""Astro session: time preview, solve-with-overrides and JSON-lines progress, as library functions.

A session is a set of photo paths plus the user's confirmed choices (clock offset, time tolerance, solve source,
site, timezone, cull rule). Nothing here knows about a photo library: per-photo facts a library holds (a GPS
position set on the photo, a manually corrected capture time) come in as ``hints``; results go out as plain dicts
through ``emit``. The ``astro-id session`` command wraps these for a caller that spawns a process; a sidecar web UI
or sync layer imports them directly.

Precedence (most specific first):
- time:     hint time (that photo's true local time) > camera EXIF + offset (session > astro-id.json) > EXIF as is
- site:     hint site (that photo) > session ``site`` > astro-id.json ``site`` > none
- timezone: session ``tz`` > astro-id.json ``timezone`` > inferred from the site > system zone
- tolerance, solve source, cull rule: session > astro-id.json > default
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

from . import config as cfgmod
from .imageio import RAW_EXT, collect, raw_twin, read_exif
from .timeutil import format_offset, get_tz, iso, parse_offset, parse_tolerance, true_time

log = logging.getLogger("astro_id")

SESSION_VERSION = 1
SAVED_KEYS = ("clock_offset", "time_tolerance_min", "timezone")


@dataclass
class SessionOptions:
    offset: str | None = None              # camera clock offset, e.g. "+2559d11h08m"; None = astro-id.json
    tolerance_min: float | None = None     # +- minutes; None = astro-id.json, else 15
    source: str | None = None              # "jpg" | "raw" | "auto"; None = astro-id.json, else jpg
    site: str | None = None                # "lat,lon[,elev_m]" for photos without their own position
    tz: str | None = None                  # IANA zone of the camera-local time
    min_confidence: int = 65
    hints: dict = field(default_factory=dict)   # {path: {"site": "lat,lon[,elev]" | None, "time": ISO | None}}
    cull: dict | None = None               # cull rule (see cull.py); None = astro-id.json "cull", else default
    overlay_dir: str | None = None
    no_overlay: bool = False
    no_fallback: bool = False
    no_quality: bool = False
    overlay_scores: bool = False
    overlay_lines: bool = True
    stack_mode: str | None = None
    stack_gap: float | None = None
    extra_flags: list = field(default_factory=list)   # further `astro-id solve` flags (tag policy...)
    include_results: bool = False          # put the full frame result in each "photo" event

    def validate(self) -> None:
        """Raise ValueError for a bad override (the CLI maps it to exit 2)."""
        if self.offset is not None and str(self.offset).strip():
            parse_offset(self.offset)
        if self.tolerance_min is not None and float(self.tolerance_min) < 0:
            raise ValueError("tolerance must be >= 0 minutes")
        if self.source is not None:
            cfgmod.parse_solve_source(self.source)
        if self.site:
            cfgmod.parse_site(self.site)
        if self.tz and get_tz(self.tz) is None:
            raise ValueError(f"unknown timezone {self.tz!r}")
        for p, h in (self.hints or {}).items():
            if h and h.get("site"):
                cfgmod.parse_site(h["site"])
            if h and h.get("time"):
                cfgmod.parse_time_flag(h["time"], self.tz or "UTC")
        if self.cull is not None:
            from .cull import validate_rule

            errs = validate_rule(self.cull)
            if errs:
                raise ValueError("; ".join(errs))


def _hint(opts: SessionOptions, path: str) -> dict:
    h = opts.hints.get(path) or opts.hints.get(str(Path(path))) or {}
    return {"site": h.get("site") or None, "time": h.get("time") or None}


def _site_dict(text: str | None) -> dict | None:
    if not text:
        return None
    parts = [float(p) for p in str(text).split(",") if p.strip()]
    return {"lat": parts[0], "lon": parts[1], "elev_m": parts[2] if len(parts) > 2 else None}


# ------------------------------------------------------------------ preview ---

def preview_one(path: str, opts: SessionOptions) -> dict:
    """What a solve of ``path`` would use, without solving (fast: EXIF + astro-id.json only)."""
    p = Path(path)
    out: dict = {"path": path, "camera_time": None, "offset": None, "offset_source": None, "true_time": None,
                 "window_start": None, "window_end": None, "tolerance_min": None, "time_source": None,
                 "timezone": None, "tz_source": None, "site": None, "site_source": "none", "raw_companion": None,
                 "solve_source": None, "config": None, "warnings": [], "error": None}
    try:
        cpath = cfgmod.find_config(p.parent)
        cfg = cfgmod.load_config(cpath)
        out["config"] = str(cpath).replace("\\", "/") if cpath else None
        hint = _hint(opts, path)
        # site
        if hint["site"]:
            out["site"], out["site_source"] = _site_dict(hint["site"]), "photo"
        elif opts.site:
            out["site"], out["site_source"] = _site_dict(opts.site), "session"
        elif cfgmod.site_from_dict(cfg.get("site")):
            s = cfg["site"]
            out["site"] = {"lat": float(s["lat"]), "lon": float(s["lon"]), "elev_m": s.get("elev_m"), "name": s.get("name")}
            out["site_source"] = "config"
        else:
            out["warnings"].append("no site: altitude checks are skipped and planets cannot be ruled out by horizon (their confidence is capped at 79, the Moon 70)")
        # timezone
        if opts.tz:
            out["timezone"], out["tz_source"] = opts.tz, "session"
        elif cfg.get("timezone"):
            out["timezone"], out["tz_source"] = cfg["timezone"], "config"
        elif out["site"]:
            tz = cfgmod.infer_timezone(out["site"]["lat"], out["site"]["lon"])
            out["timezone"], out["tz_source"] = tz, "site" if tz else "system"
        else:
            out["tz_source"] = "system"
        # tolerance
        tol = (float(opts.tolerance_min) if opts.tolerance_min is not None
               else float(cfg.get("time_tolerance_min", cfgmod.DEFAULT_TOLERANCE_MIN)))
        out["tolerance_min"] = int(tol) if tol.is_integer() else tol
        # offset + time
        if opts.offset is not None:
            off_txt, out["offset_source"] = opts.offset, "session"
        elif "clock_offset" in cfg:
            off_txt, out["offset_source"] = cfg.get("clock_offset"), "config"
        else:
            off_txt = None
        off = parse_offset(off_txt) if off_txt not in (None, "") else timedelta(0)
        out["offset"] = format_offset(off) if out["offset_source"] else None
        exif = read_exif(p)
        out["camera_time"] = exif.datetime_original.isoformat() if exif.datetime_original else None
        if hint["time"]:
            t = cfgmod.parse_time_flag(hint["time"], out["timezone"])
            out["time_source"] = "facet"
        elif exif.datetime_original is not None:
            t = true_time(exif.datetime_original, off, out["timezone"])
            out["time_source"] = "config" if out["offset_source"] else "exif"
            if not out["offset_source"]:
                out["warnings"].append("no clock offset: EXIF time used as is (Sun, Moon, planets capped at candidate)")
        else:
            t = None
            out["warnings"].append("no EXIF capture time")
        if t is not None:
            out["true_time"] = iso(t)
            out["window_start"] = iso(t - timedelta(minutes=tol))
            out["window_end"] = iso(t + timedelta(minutes=tol))
        # solve source
        want = cfgmod.parse_solve_source(opts.source or cfg.get("solve_source"))
        twin = None if p.suffix.lower() in RAW_EXT else raw_twin(p)
        out["raw_companion"] = str(twin).replace("\\", "/") if twin else None
        if p.suffix.lower() in RAW_EXT:
            out["solve_source"] = "raw"
        elif want == "jpg" or twin is None:
            out["solve_source"] = "jpg"
            if want == "raw" and twin is None:
                out["warnings"].append("solve source raw requested but there is no RAW companion: the JPG is solved")
        else:
            out["solve_source"] = "raw"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def time_preview(paths: list[str], opts: SessionOptions, emit: Callable[[dict], None] | None = None) -> list[dict]:
    """Preview every path; ``emit`` (optional) receives start / preview / done events."""
    t0 = time.time()
    if emit:
        emit({"event": "start", "mode": "preview", "total": len(paths), "session": SESSION_VERSION})
    out = []
    for i, p in enumerate(paths):
        r = preview_one(p, opts)
        out.append(r)
        if emit:
            emit({"event": "preview", "index": i, **r})
    if emit:
        emit({"event": "done", "mode": "preview", "total": len(paths),
              "errors": sum(1 for r in out if r["error"]), "elapsed_s": round(time.time() - t0, 1)})
    return out


# -------------------------------------------------------------- folder config ---

def save_folder_config(folders: list[str], opts: SessionOptions, extra: dict | None = None) -> list[str]:
    """Write the session's offset / tolerance / timezone (+ ``extra`` keys; a None value removes that key) into
    each folder's own astro-id.json.

    An existing file in the folder is merged (other keys kept). A folder that only inherits a parent's file gets
    a new one copied from it (so the parent's site keeps applying: the nearest file wins whole)."""
    written = []
    for folder in sorted({str(Path(f)) for f in folders}):
        d = Path(folder)
        target = d / cfgmod.CONFIG_NAME
        base = cfgmod.load_config(cfgmod.find_config(d))
        new = dict(base)
        if opts.offset is not None:
            new["clock_offset"] = format_offset(parse_offset(opts.offset)) if str(opts.offset).strip() else ""
        if opts.tolerance_min is not None:
            tol = float(opts.tolerance_min)
            new["time_tolerance_min"] = int(tol) if tol.is_integer() else tol
        if opts.tz:
            new["timezone"] = opts.tz
        if opts.source in ("jpg", "raw", "auto"):
            new["solve_source"] = opts.source
        for k, v in (extra or {}).items():   # panel settings kept per folder (labeled_copies, sky_labels)
            if v is None:
                new.pop(k, None)             # None removes a key (e.g. the older "overlay", now "labeled_copies")
            else:
                new[k] = v
        if new == cfgmod.load_config(target if target.is_file() else None) and target.is_file():
            continue
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        written.append(str(target).replace("\\", "/"))
    return written


# --------------------------------------------------------------------- solve ---

def _solve_args(target: Path, opts: SessionOptions, site: str | None, time_hint: str | None):
    from .cli import build_parser

    argv = ["solve", str(target), "--min-confidence", str(int(opts.min_confidence))]
    if opts.offset is not None:
        argv += ["--offset", str(opts.offset)]
    if opts.tolerance_min is not None:
        argv += ["--time-tolerance", f"{float(opts.tolerance_min):g}m"]
    if opts.source:
        argv += ["--solve-source", opts.source]
    if site:
        argv += ["--site", site]
    if opts.tz:
        argv += ["--timezone", opts.tz]
    if time_hint:
        argv += ["--time", time_hint]
    if opts.overlay_dir:
        argv += ["--overlay-dir", opts.overlay_dir]
    for flag, on in (("--no-overlay", opts.no_overlay), ("--no-fallback", opts.no_fallback),
                     ("--no-quality", opts.no_quality), ("--overlay-scores", opts.overlay_scores),
                     ("--no-overlay-lines", not opts.overlay_lines)):
        if on:
            argv.append(flag)
    if opts.stack_mode:
        argv += ["--stack-mode", opts.stack_mode]
    if opts.stack_gap is not None:
        argv += ["--stack-gap", str(opts.stack_gap)]
    argv += [str(x) for x in opts.extra_flags]
    return build_parser().parse_args(argv)


def plan_jobs(paths: list[str], opts: SessionOptions) -> list[dict]:
    """Group the photos into engine runs: one per (folder, site) for photos without their own time (so a
    stacking sequence stays together), one per photo with a hint time. Each job: {folder, site, time, files,
    requested} where ``files`` are the images solved (a RAW maps to its same-stem JPG) and ``requested`` the
    caller's paths in the same order."""
    groups: dict = {}
    jobs = []
    for p in paths:
        h = _hint(opts, p)
        site = h["site"] or opts.site
        f = collect(Path(p))[0] if Path(p).exists() else Path(p)
        if h["time"]:
            jobs.append({"folder": str(Path(p).parent), "site": site, "time": h["time"], "files": [f], "requested": [p]})
        else:
            g = groups.setdefault((str(Path(p).parent), site), {"folder": str(Path(p).parent), "site": site,
                                                                "time": None, "files": [], "requested": []})
            g["files"].append(f)
            g["requested"].append(p)
    return list(groups.values()) + jobs


def _status(fr: dict) -> str:
    if fr.get("solver") == "stack-copy":
        return "stack-copy"
    return "solved" if fr.get("solved") else "failed"


def photo_event(path: str, index: int, fr: dict, opts: SessionOptions) -> dict:
    n = sum(1 for o in fr.get("objects") or []
            if o.get("taggable", True) is not False and (o.get("confidence") or 0) >= opts.min_confidence)
    ev = {"event": "photo", "index": index, "path": path, "file": fr.get("file"), "status": _status(fr),
          "objects": n, "grade": fr.get("grade"), "stars": fr.get("stars"), "rms_px": fr.get("rms_px"),
          "solve_source": fr.get("solve_source"), "stack_of": fr.get("stack_of"), "true_time": fr.get("true_time"),
          "time_source": fr.get("time_source"), "tolerance_min": fr.get("time_tolerance_min"),
          "best_fit_time": fr.get("best_fit_time"), "best_fit_delta_min": fr.get("best_fit_delta_min"),
          "time_flag": fr.get("time_flag"), "time_hint": fr.get("time_hint"), "quality": fr.get("quality"),
          "overlay": fr.get("overlay"),
          "error": fr.get("error")}
    if opts.include_results:
        ev["result"] = fr
    return ev


def time_summary(results: list[dict], tolerance_min: float | None) -> dict:
    """How well the session's time is pinned: best-fit (Moon) frames and a suggested tighter tolerance."""
    deltas = [r["best_fit_delta_min"] for r in results if isinstance(r.get("best_fit_delta_min"), (int, float))]
    tols = [r["time_tolerance_min"] for r in results if isinstance(r.get("time_tolerance_min"), (int, float))]
    tol = tolerance_min if tolerance_min is not None else (max(tols) if tols else None)
    out = {"tolerance_min": tol, "best_fit_frames": len(deltas), "max_abs_delta_min": None,
           "suggest_tolerance_min": None, "outside_tolerance": sum(1 for r in results if r.get("time_flag"))}
    if deltas:
        m = max(abs(d) for d in deltas)
        out["max_abs_delta_min"] = round(m, 1)
        sug = int(math.ceil(m + 2))   # 2 min of slack over the worst observed best-fit offset
        if tol is None or sug < tol:
            out["suggest_tolerance_min"] = sug
    return out


def run_session(paths: list[str], opts: SessionOptions, emit: Callable[[dict], None] | None = None,
                save_config: bool = False, solve_files_fn=None) -> dict:
    """Solve ``paths`` with the session overrides. Emits start, (config_saved), photo per frame as it finishes,
    and done (with the session's cull verdicts and time summary). Returns the done event plus ``results``
    ({path: frame result})."""
    from . import cull as cullmod
    from .cli import solve_files

    solve_files_fn = solve_files_fn or solve_files
    emit = emit or (lambda ev: None)
    t0 = time.time()
    opts.validate()
    emit({"event": "start", "mode": "solve", "total": len(paths), "session": SESSION_VERSION})
    if save_config:
        for w in save_folder_config([str(Path(p).parent) for p in paths], opts):
            emit({"event": "config_saved", "path": w})
    results: dict = {}
    index = {p: i for i, p in enumerate(paths)}
    for job in plan_jobs(paths, opts):
        by_file = {str(f): r for f, r in zip(job["files"], job["requested"])}
        folder = Path(job["folder"])
        try:
            args = _solve_args(job["files"][0] if job["time"] else folder, opts, job["site"], job["time"])
            stack_cfg = cfgmod.stack_config(folder, opts.stack_mode, opts.stack_gap)
        except (ValueError, SystemExit) as exc:
            err = f"bad session option: {exc}"
            for r in job["requested"]:
                fr = {"file": r, "solved": False, "solver": None, "error": err, "objects": []}
                results[r] = fr
                emit(photo_event(r, index[r], fr, opts))
            continue
        missing = [f for f in job["files"] if not Path(f).exists()]
        for f in missing:
            r = by_file[str(f)]
            results[r] = {"file": str(f), "solved": False, "solver": None, "error": "file not found", "objects": []}
            emit(photo_event(r, index[r], results[r], opts))
        files = [f for f in job["files"] if Path(f).exists()]
        if not files:
            continue

        def on_frame(fr, _by=by_file):
            key = next((k for k in _by if _norm_path(k) == _norm_path(fr.get("file") or "")), None)
            if key is None:
                return
            r = _by[key]
            results[r] = fr
            emit(photo_event(r, index[r], fr, opts))
        try:
            solve_files_fn(files, args, stack_cfg, on_frame)
        except Exception as exc:   # the engine failed as a whole for this job
            log.exception("session job %s failed", folder)
            for f in files:
                r = by_file[str(f)]
                if r not in results:
                    results[r] = {"file": str(f), "solved": False, "solver": None,
                                  "error": f"{type(exc).__name__}: {exc}"[:200], "objects": []}
                    emit(photo_event(r, index[r], results[r], opts))
    rule = opts.cull
    if rule is None and paths:
        rule = cfgmod.load_config(cfgmod.find_config(Path(paths[0]).parent)).get("cull")
    cull = cullmod.cull_session({p: (results.get(p) or {}).get("quality") for p in paths}, rule,
                                {p: bool((results.get(p) or {}).get("solved")) for p in paths})
    ordered = [results[p] for p in paths if p in results]
    done = {"event": "done", "mode": "solve", "total": len(paths),
            "solved": sum(1 for r in ordered if r.get("solved") and r.get("solver") != "stack-copy"),
            "stack_copies": sum(1 for r in ordered if r.get("solver") == "stack-copy"),
            "failed": sum(1 for r in ordered if not r.get("solved")) + (len(paths) - len(ordered)),
            "elapsed_s": round(time.time() - t0, 1),
            "time": time_summary(ordered, opts.tolerance_min),
            "cull": {"reference": cull["reference"], "rule": cull["rule"],
                     "frames": {p: {"decision": v["decision"], "reasons": v["reasons"]}
                                for p, v in cull["frames"].items()}}}
    emit(done)
    return {**done, "results": results}


def _norm_path(p: str) -> str:
    """Path key for matching an engine result to the file it solved (case- and slash-insensitive)."""
    s = str(p).replace("\\", "/")
    if not s.startswith("//"):
        try:
            s = str(Path(s).resolve()).replace("\\", "/")
        except OSError:
            pass
    return os.path.normcase(s).replace("\\", "/")


def recull(metrics_by_path: dict, rule: dict | None = None, solved_by_path: dict | None = None) -> dict:
    """Re-run only the culling (the user changed the rule): no solving, no I/O."""
    from .cull import cull_session

    return cull_session(metrics_by_path, rule, solved_by_path)
