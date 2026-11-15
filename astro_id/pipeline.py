"""Per-frame pipeline: EXIF time -> solve -> identify -> overlay -> contract dict + CSV rows."""
from __future__ import annotations

import csv
import json
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from . import config as cfgmod
from .identify import Identification, identify
from .imageio import choose_source, read_exif
from .annotations import build_annotations, legend_text
from .overlay import draw_overlay
from .solve import solve_image
from .timeutil import format_offset, iso, true_time

log = logging.getLogger("astro_id")

CSV_NAME = "astro-id-report.csv"
RESULTS_NAME = "astro-id-results.json"
CONTRACT_VERSION = 1
CSV_FIELDS = ["file", "true_time", "time_source", "tolerance_min", "best_fit_time", "time_flag", "solved",
              "solver", "grade", "stars", "rms_px", "lim_mag", "center_ra_deg", "center_dec_deg", "object",
              "kind", "confidence", "tier", "level", "magnitude", "parent", "member_of", "taggable", "x", "y",
              "alt_min_deg", "sep_to_source_deg", "stack_of", "error"]


def posix(p: Path | str | None) -> str | None:
    if p is None:
        return None
    s = str(p)
    if not s.startswith("\\\\"):
        try:
            s = str(Path(s).resolve())
        except OSError:
            pass
    return s.replace("\\", "/")


def default_overlay_dir(folder: Path) -> Path:
    return folder.parent / f"{folder.name}-annotated"


def empty_frame(path: Path) -> dict:
    return {
        "file": posix(path), "solved": False, "solver": None, "grade": None, "rms_px": None, "stars": 0,
        "center_ra_deg": None, "center_dec_deg": None, "fov_deg": None, "true_time": None,
        "time_tolerance_min": None, "time_source": "exif", "best_fit_time": None, "time_flag": None,
        "overlay": None, "error": None, "stack_of": None, "objects": [], "annotations": None, "solve_source": None,
        "best_fit_delta_min": None, "quality": None, "warnings": [],
    }


WARN_NO_OFFSET = ("no clock offset configured: the EXIF time is used as is, so Sun, Moon and planets are "
                  "capped at candidate")
WARN_NO_SITE = ("no site (position): Sun, Moon and planets are computed from the geocentre (the Moon can be up to "
                "~1 deg off) and nothing is checked against the horizon; no default site is assumed")
WARN_NO_EXIF_TIME = ("no EXIF DateTimeOriginal: Sun, Moon and planets were computed for the current clock time "
                     "and are capped at candidate")
_warned: set[str] = set()   # each distinct message is logged once per run; every frame still carries it


def reset_warnings() -> None:
    _warned.clear()


def _warn(fr: dict, msg: str) -> None:
    fr.setdefault("warnings", []).append(msg)
    if msg not in _warned:
        _warned.add(msg)
        log.warning("%s", msg)


def _quality(path: Path, fr: dict, scale_deg: float | None, matched: int | None, enabled: bool) -> None:
    """Fill ``fr["quality"]`` (never raises: metrics are an extra, the frame keeps its tags without them)."""
    if not enabled:
        return
    try:
        from .quality import frame_quality

        fr["quality"] = frame_quality(path, scale_deg * 3600 if scale_deg else None, matched)
    except Exception as exc:
        log.warning("%s: quality metrics failed (%s)", path.name, exc)
        log.debug(traceback.format_exc())


def process(path: Path, offset_flag, tol_flag, site_flag, overlay_dir: Path | None, make_overlay: bool,
            use_fallback: bool, time_flag: str | None = None,
            tz_flag: str | None = None, policy_flags: dict | None = None, min_confidence: int = 65,
            ctx: dict | None = None, overlay_scores: bool = False,
            solve_source: str | None = None, quality: bool = True,
            overlay_lines: bool = True) -> tuple[dict, list[dict]]:
    """Solve and identify one frame. ``ctx`` (if given) receives the solution and identification,
    so a stacking sequence can reuse them."""
    fr = empty_frame(path)
    rows: list[dict] = []
    try:
        fc, exif, t_true = _frame_time(path, fr, offset_flag, tol_flag, site_flag, time_flag, tz_flag, policy_flags,
                                       solve_source)
        t_ep = t_true or datetime.now().astimezone()
        epoch = t_ep.year + (t_ep.timetuple().tm_yday - 0.5) / 365.25
        src, crop = choose_source(path, exif, fc.solve_source)
        fr["solve_source"] = "raw" if src != path else "jpg"
        sol = solve_image(path, exif, use_fallback=use_fallback, epoch=epoch, source=src, raw_crop=crop)
        for a in sol.attempts:
            log.debug("  %s", a)
        fr["stars"] = int(sol.matched if sol.solved else (len(sol.det) if sol.det is not None else 0))
        if sol.solved:
            from .stack import _center_scale_deg

            scale = _center_scale_deg(sol)
        else:
            from .solve import pixel_scale_deg

            scale = pixel_scale_deg(exif, exif.width, exif.height)
        _quality(path, fr, scale, int(sol.matched) if sol.solved else None, quality)
        if not sol.solved:
            fr["error"] = sol.error or "no plate solution"
            log.info("%s: not solved (%s)", path.name, fr["error"])
            rows.append(_row(fr, None, sol))
            return fr, rows
        ra, dec = sol.center
        fw, fh = sol.fov_deg()
        fr.update(solved=True, solver=sol.solver, grade=sol.grade, rms_px=sol.rms_px, center_ra_deg=round(ra, 3),
                  center_dec_deg=round(dec, 3), fov_deg=[round(fw, 1), round(fh, 1)])
        if t_true is None:
            fr["error"] = "no EXIF DateTimeOriginal: Sun, Moon and planets computed for the current clock time"
            t_true = datetime.now().astimezone()
        ident: Identification = identify(sol, t_true, fc.tolerance_min, fc.site, fc.time_source, fc.policy,
                                         min_confidence)
        if ctx is not None:
            ctx.update(sol=sol, ident=ident)
        fr["objects"] = [o.as_dict() for o in ident.objects]
        fr["best_fit_time"] = iso(ident.best_fit_time.astimezone(t_true.tzinfo)) if ident.best_fit_time else None
        if ident.best_fit_time and fr["true_time"]:
            fr["best_fit_delta_min"] = round((ident.best_fit_time - t_true).total_seconds() / 60, 1)
        fr["time_flag"] = ident.time_flag
        fr["annotations"] = _annotations(ident.objects, sol, sol.width, sol.height, legend_text(
            t_true if fr["true_time"] else None, fr["time_tolerance_min"], sol.solver, sol.grade, fr["stars"]))
        if make_overlay:
            od = overlay_dir or default_overlay_dir(path.parent)
            out = od / f"{path.stem}_astro.jpg"
            legend = [
                path.name,
                f"time {fr['true_time']} +/- {fr['time_tolerance_min']} min ({fc.time_source})",
                f"solve {sol.solver} grade {sol.grade}: {sol.matched} stars, rms {sol.rms_px} px",
            ]
            if fr["best_fit_time"]:
                legend.append(f"best-fit time (Moon) {fr['best_fit_time']}" + (" OUTSIDE tolerance" if fr["time_flag"] else ""))
            draw_overlay(path, out, sol.width, ident.objects, legend, scores=overlay_scores, lines=overlay_lines)
            fr["overlay"] = posix(out)
        for o in ident.objects:
            rows.append(_row(fr, o, sol))
        log.info("%s: solved %s grade %s, %d stars, rms %.2f px, %d objects", path.name, sol.solver, sol.grade,
                 sol.matched, sol.rms_px, len(ident.objects))
        for name, c in ident.checks.items():
            log.info("  %s: predicted vs nearest detected source %.3f deg (%s)", name, c["sep_deg"],
                     "found" if c["found"] else "not found")
    except Exception as exc:  # one bad frame never stops the run
        log.error("%s: %s", path.name, exc)
        log.debug(traceback.format_exc())
        fr["error"] = f"{type(exc).__name__}: {exc}"[:200]
        rows.append(_row(fr, None, None))
    return fr, rows


def _parse_iso(v):
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def _annotations(objs, sol, width, height, legend, xform=None):
    """The annotations payload; a failure here never costs the frame its tags."""
    try:
        return build_annotations(objs, sol, width, height, legend, xform)
    except Exception as exc:
        log.warning("annotations failed (%s)", exc)
        log.debug(traceback.format_exc())
        return None


def _frame_time(path: Path, fr: dict, offset_flag, tol_flag, site_flag, time_flag, tz_flag, policy_flags,
                solve_source=None):
    fc = cfgmod.resolve(path.parent, offset_flag, tol_flag, site_flag, time_flag, tz_flag, policy_flags,
                        solve_source)
    exif = read_exif(path)
    fr["time_tolerance_min"] = int(fc.tolerance_min) if float(fc.tolerance_min).is_integer() else fc.tolerance_min
    fr["time_source"] = fc.time_source
    t_true = None
    if fc.time_override is not None:
        t_true = fc.time_override
        fr["true_time"] = iso(t_true)
    elif exif.datetime_original is not None:
        t_true = true_time(exif.datetime_original, fc.offset, fc.timezone)
        fr["true_time"] = iso(t_true)
    log.info("%s: true time %s (offset %s, %s)", path.name, fr["true_time"], format_offset(fc.offset), fc.time_source)
    if fc.time_override is None and exif.datetime_original is None:
        _warn(fr, WARN_NO_EXIF_TIME)
    elif fc.time_source == "exif":
        _warn(fr, WARN_NO_OFFSET)
    if fc.site is None:
        _warn(fr, WARN_NO_SITE)
    return fc, exif, t_true


def process_copy(path: Path, rep_fr: dict, rep_sol, rep_ident: Identification, m, offset_flag, tol_flag, site_flag,
                 overlay_dir: Path | None, make_overlay: bool, tz_flag: str | None = None,
                 policy_flags: dict | None = None, overlay_scores: bool = False,
                 quality: bool = True, overlay_lines: bool = True) -> tuple[dict, list[dict]]:
    """A stacking-sequence frame whose pointing matches the representative: reuse its objects."""
    from .stack import map_objects

    fr = empty_frame(path)
    rows: list[dict] = []
    try:
        _frame_time(path, fr, offset_flag, tol_flag, site_flag, None, tz_flag, policy_flags)
        objs = map_objects(rep_ident.objects, m, rep_sol.width, rep_sol.height)
        cx, cy = m.inverse([(rep_sol.width - 1) / 2], [(rep_sol.height - 1) / 2])
        c = rep_sol.wcs.pixel_to_world(float(cx[0]), float(cy[0]))
        fr.update(solved=True, solver="stack-copy", grade=rep_fr["grade"], rms_px=round(m.rms_px, 2),
                  stars=int(m.pairs), center_ra_deg=round(float(c.ra.deg), 3), center_dec_deg=round(float(c.dec.deg), 3),
                  fov_deg=rep_fr["fov_deg"], solve_source=rep_fr.get("solve_source"), best_fit_time=None, time_flag=None, stack_of=rep_fr["file"],
                  objects=[o.as_dict() for o in objs])
        from .stack import _center_scale_deg

        _quality(path, fr, _center_scale_deg(rep_sol), int(m.pairs), quality)
        log.info("%s: stack-copy of %s (shift %.2f deg, %d pairs)", path.name, Path(rep_fr["file"]).name,
                 m.offset_deg, m.pairs)
        fr["annotations"] = _annotations(objs, rep_sol, rep_sol.width, rep_sol.height, legend_text(
            _parse_iso(fr["true_time"]), fr["time_tolerance_min"], "stack-copy", rep_fr["grade"], int(m.pairs)),
            xform=m.apply)
        if make_overlay:
            od = overlay_dir or default_overlay_dir(path.parent)
            out = od / f"{path.stem}_astro.jpg"
            legend = [path.name, f"time {fr['true_time']} +/- {fr['time_tolerance_min']} min ({fr['time_source']})",
                      f"stack-copy of {Path(rep_fr['file']).name}: shift {m.offset_deg:.2f} deg, {m.pairs} stars matched"]
            draw_overlay(path, out, rep_sol.width, objs, legend, scores=overlay_scores, lines=overlay_lines)
            fr["overlay"] = posix(out)
        for o in objs:
            rows.append(_row(fr, o, rep_sol))
    except Exception as exc:
        log.error("%s: %s", path.name, exc)
        log.debug(traceback.format_exc())
        fr = empty_frame(path) | {"error": f"{type(exc).__name__}: {exc}"[:200]}
        rows = [_row(fr, None, None)]
    return fr, rows


def _row(fr: dict, o, sol) -> dict:
    return {
        "file": Path(fr["file"]).name, "true_time": fr["true_time"], "time_source": fr["time_source"],
        "tolerance_min": fr["time_tolerance_min"], "best_fit_time": fr["best_fit_time"], "time_flag": fr["time_flag"],
        "solved": fr["solved"], "solver": fr["solver"], "grade": fr["grade"], "stars": fr["stars"],
        "rms_px": fr["rms_px"], "lim_mag": round(sol.lim_mag, 2) if sol is not None and sol.lim_mag else None,
        "center_ra_deg": fr["center_ra_deg"], "center_dec_deg": fr["center_dec_deg"],
        "object": o.name if o else None, "kind": o.kind if o else None, "confidence": o.confidence if o else None,
        "tier": o.tier if o else None, "level": o.level if o else None,
        "magnitude": None if o is None or o.mag is None or o.kind == "constellation" else round(o.mag, 2),
        "parent": o.parent if o else None, "member_of": o.member_of if o else None,
        "taggable": o.taggable if o else None, "x": None if o is None or o.x is None else round(o.x, 1),
        "y": None if o is None or o.y is None else round(o.y, 1),
        "alt_min_deg": None if o is None or o.alt_min is None else round(o.alt_min, 2),
        "sep_to_source_deg": None if o is None or o.sep_deg is None else round(o.sep_deg, 3),
        "stack_of": Path(fr["stack_of"]).name if fr.get("stack_of") else None, "error": fr["error"],
    }


def write_csv(path: Path, rows: list[dict]) -> Path:
    """Write the report, keeping rows of other files already in it: a single-file run (the Facet
    catch-up) replaces only its own frames, never the rest of the folder's report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = {r["file"] for r in rows}
    keep: list[dict] = []
    if path.exists():
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                keep = [r for r in csv.DictReader(fh) if r.get("file") and r["file"] not in mine]
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            log.warning("could not read existing report %s (%s); rewriting it", path, exc)
            keep = []
    merged = sorted(keep + list(rows), key=lambda r: str(r.get("file") or ""))  # stable: object order kept
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, restval="", extrasaction="ignore")
        w.writeheader()
        for r in merged:
            w.writerow(r)
    return path


def write_results(path: Path, frames: list[dict]) -> Path:
    """Write the per-frame results next to the CSV report: ``{"contract": 1, "results": [<frame>, ...]}``, the same
    document ``solve --json`` prints. Like the report it keeps entries of other files already there (a single-file
    run replaces only its own frame), sorted by file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mine = {f["file"] for f in frames}
    keep: list[dict] = []
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            keep = [r for r in old.get("results", []) if isinstance(r, dict) and r.get("file") not in mine]
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("could not read existing results %s (%s); rewriting it", path, exc)
    merged = sorted(keep + list(frames), key=lambda r: str(r.get("file") or ""))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"contract": CONTRACT_VERSION, "results": merged}, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
