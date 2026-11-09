"""Per-frame pipeline: EXIF time -> solve -> identify -> overlay -> contract dict + CSV rows."""
from __future__ import annotations

import csv
import logging
import traceback
from datetime import datetime
from pathlib import Path

from . import config as cfgmod
from .identify import Identification, identify
from .imageio import read_exif
from .overlay import draw_overlay
from .solve import solve_image
from .timeutil import format_offset, iso, true_time

log = logging.getLogger("astro_id")

CSV_NAME = "astro-id-report.csv"
CSV_FIELDS = ["file", "true_time", "time_source", "tolerance_min", "best_fit_time", "time_flag", "solved",
              "solver", "grade", "stars", "rms_px", "lim_mag", "center_ra_deg", "center_dec_deg", "object",
              "kind", "confidence", "tier", "x", "y", "alt_min_deg", "sep_to_source_deg", "error"]


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
        "overlay": None, "error": None, "objects": [],
    }


def process(path: Path, offset_flag, tol_flag, site_flag, overlay_dir: Path | None, make_overlay: bool,
            use_fallback: bool, time_flag: str | None = None,
            tz_flag: str | None = None) -> tuple[dict, list[dict]]:
    fr = empty_frame(path)
    rows: list[dict] = []
    try:
        fc = cfgmod.resolve(path.parent, offset_flag, tol_flag, site_flag, time_flag, tz_flag)
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
        t_ep = t_true or datetime.now().astimezone()
        epoch = t_ep.year + (t_ep.timetuple().tm_yday - 0.5) / 365.25
        sol = solve_image(path, exif, use_fallback=use_fallback, epoch=epoch)
        for a in sol.attempts:
            log.debug("  %s", a)
        fr["stars"] = int(sol.matched if sol.solved else (len(sol.det) if sol.det is not None else 0))
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
            fr["error"] = "no EXIF DateTimeOriginal: time-dependent objects skipped"
            t_true = datetime.now().astimezone()
        ident: Identification = identify(sol, t_true, fc.tolerance_min, fc.site, fc.time_source)
        fr["objects"] = [o.as_dict() for o in ident.objects]
        fr["best_fit_time"] = iso(ident.best_fit_time.astimezone(t_true.tzinfo)) if ident.best_fit_time else None
        fr["time_flag"] = ident.time_flag
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
            draw_overlay(path, out, sol.width, ident.objects, legend)
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


def _row(fr: dict, o, sol) -> dict:
    return {
        "file": Path(fr["file"]).name, "true_time": fr["true_time"], "time_source": fr["time_source"],
        "tolerance_min": fr["time_tolerance_min"], "best_fit_time": fr["best_fit_time"], "time_flag": fr["time_flag"],
        "solved": fr["solved"], "solver": fr["solver"], "grade": fr["grade"], "stars": fr["stars"],
        "rms_px": fr["rms_px"], "lim_mag": round(sol.lim_mag, 2) if sol is not None and sol.lim_mag else None,
        "center_ra_deg": fr["center_ra_deg"], "center_dec_deg": fr["center_dec_deg"],
        "object": o.name if o else None, "kind": o.kind if o else None, "confidence": o.confidence if o else None,
        "tier": o.tier if o else None, "x": None if o is None or o.x is None else round(o.x, 1),
        "y": None if o is None or o.y is None else round(o.y, 1),
        "alt_min_deg": None if o is None or o.alt_min is None else round(o.alt_min, 2),
        "sep_to_source_deg": None if o is None or o.sep_deg is None else round(o.sep_deg, 3),
        "error": fr["error"],
    }


def write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path
