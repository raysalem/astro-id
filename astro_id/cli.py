"""Command line: `astro-id solve ...` and `astro-id site <folder>` (contract v1, see CONTRACT.md)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

CONTRACT_VERSION = 1
log = logging.getLogger("astro_id")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="astro-id", description="Plate-solve night-sky photos and name the objects in them.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve", help="solve a photo or every photo in a folder")
    s.add_argument("target", help="image file or folder")
    s.add_argument("--min-confidence", type=int, default=65,
                   help="report cut used by consumers (default 65); JSON always lists every object")
    s.add_argument("--offset", help="camera clock offset added to EXIF time, e.g. +2559d11h08m")
    s.add_argument("--time-tolerance", help="time uncertainty, e.g. 15m (default 15m or astro-id.json)")
    s.add_argument("--site", help="lat,lon[,elev_m] (elevation is looked up on USGS EPQS when omitted)")
    s.add_argument("--time", help="true local time of a single photo, ISO-8601 (overrides EXIF + offset; "
                                  "time_source becomes 'facet')")
    s.add_argument("--timezone", help="IANA zone for a naive --time (default: astro-id.json timezone, else system)")
    s.add_argument("--overlay-dir", help="where overlays and the CSV report go (default: <folder>-annotated)")
    s.add_argument("--json", action="store_true", help="print the contract JSON to stdout")
    s.add_argument("--no-overlay", action="store_true")
    s.add_argument("--no-fallback", action="store_true", help="never use nova.astrometry.net")
    s.add_argument("-v", "--verbose", action="store_true")
    t = sub.add_parser("site", help="pick the observing site on Google Maps -> astro-id.json")
    t.add_argument("folder")
    d = sub.add_parser("setup", help="download reference data and check for ASTAP")
    d.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(message)s")
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.cmd == "site":
        from .site import run_site

        return run_site(Path(args.folder))
    if args.cmd == "setup":
        return cmd_setup()
    return cmd_solve(args)


def cmd_setup() -> int:
    from . import data
    from .solve import available_databases, find_astap

    st = data.ensure_all()
    for k, v in st.items():
        log.info("%-10s %s", k, v)
    exe, db = find_astap()
    log.info("astap      %s", exe or "NOT FOUND (set ASTAP_PATH)")
    log.info("databases  %s", ", ".join(sorted(available_databases(db))) or "none found")
    return 0 if exe and all(v == "ok" for v in st.values()) else 3


def cmd_solve(args) -> int:
    from . import config as cfgmod
    from .imageio import collect
    from .pipeline import CSV_NAME, default_overlay_dir, process, write_csv
    from .timeutil import parse_offset, parse_tolerance

    target = Path(args.target)
    if not target.exists():
        log.error("not found: %s", target)
        return 2
    try:  # validate flags up front: usage errors are the only non-zero exits
        if args.offset is not None:
            parse_offset(args.offset)
        if args.time_tolerance is not None:
            parse_tolerance(args.time_tolerance)
        if args.site:
            cfgmod.parse_site(args.site)
        if args.time:
            if target.is_dir():
                raise ValueError("--time applies to a single file, not a folder")
            cfgmod.parse_time_flag(args.time, args.timezone)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    from .solve import find_astap

    if find_astap()[0] is None:
        log.warning("ASTAP not found (set ASTAP_PATH); only the astrometry.net fallback can solve")
    files = collect(target)
    if not files:
        log.error("no images in %s", target)
        return 2
    overlay_dir = Path(args.overlay_dir) if args.overlay_dir else None
    results, rows_by_dir = [], {}
    for i, f in enumerate(files, 1):
        log.info("[%d/%d] %s", i, len(files), f.name)
        fr, rows = process(f, args.offset, args.time_tolerance, args.site, overlay_dir, not args.no_overlay,
                           not args.no_fallback, args.time, args.timezone)
        results.append(fr)
        od = overlay_dir or default_overlay_dir(f.parent)
        rows_by_dir.setdefault(od, []).extend(rows)
    for od, rows in rows_by_dir.items():
        try:
            p = write_csv(od / CSV_NAME, rows)
            log.info("report: %s", p)
        except OSError as exc:
            log.warning("could not write report in %s: %s", od, exc)
    solved = sum(r["solved"] for r in results)
    log.info("done: %d/%d solved", solved, len(results))
    if args.json:
        sys.stdout.write(json.dumps({"contract": CONTRACT_VERSION, "results": results}, indent=1) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
