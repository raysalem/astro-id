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
    s.add_argument("--overlay-scores", action="store_true",
                   help="print the confidence after each name on the overlay JPG (default: names only)")
    s.add_argument("--solve-source", choices=["jpg", "raw", "auto"],
                   help="pixels the solver reads: jpg (default), raw (the same-stem RAW, cropped to the JPG frame), "
                        "auto (raw when a RAW twin exists, else jpg); results stay in JPG pixel space")
    s.add_argument("--json", action="store_true", help="print the contract JSON to stdout")
    s.add_argument("--no-overlay", action="store_true")
    s.add_argument("--no-fallback", action="store_true", help="never use nova.astrometry.net")
    g = s.add_argument_group("tag policy (default: astro-id.json tag_policy, else levels 1-4, stars off)")
    g.add_argument("--tag-levels", help="levels that become tags, e.g. 1,2,3,4 (1 constellation, 2 Sun/Moon/planet, "
                                        "3 Messier/NGC/IC, 4 asterism, 5 named star)")
    g.add_argument("--star-mag-limit", type=float,
                   help="tag named stars at least this bright (e.g. 1.5 = the ~20 brightest); turns level 5 on")
    g.add_argument("--max-catalog-mag", type=float, help="faintest catalog object (level 3) to tag (default 6.0)")
    g.add_argument("--tag-style", choices=["plain", "kind"],
                   help="plain: 'orion'; kind: 'constellation/orion', 'planet/jupiter' (default plain)")
    k = s.add_argument_group("stacking sequences (folder runs)")
    k.add_argument("--stack-mode", choices=["representative", "all"],
                   help="representative: solve the middle frame of a sequence and reuse it for frames that "
                        "point the same way (default); all: solve every frame")
    k.add_argument("--stack-gap", type=float, help="max seconds between frames of one sequence (default 120)")
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


def policy_flags(args) -> dict:
    return {"levels": args.tag_levels, "star_mag_limit": args.star_mag_limit,
            "max_catalog_mag": args.max_catalog_mag, "tag_style": args.tag_style}


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
        from .tagpolicy import make_policy

        make_policy(None, **policy_flags(args))
        if args.solve_source:
            cfgmod.parse_solve_source(args.solve_source)
        stack_cfg = cfgmod.stack_config(target if target.is_dir() else target.parent, args.stack_mode, args.stack_gap)
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
    pf = policy_flags(args)
    by_file: dict = {}
    rows_by_file: dict = {}

    def run_one(f, ctx=None):
        fr, rows = process(f, args.offset, args.time_tolerance, args.site, overlay_dir, not args.no_overlay,
                           not args.no_fallback, args.time, args.timezone, pf, args.min_confidence, ctx,
                           args.overlay_scores, args.solve_source)
        by_file[f], rows_by_file[f] = fr, rows

    groups = [[f] for f in files]
    exifs: dict = {}
    if stack_cfg.mode == "representative" and len(files) > 1:
        from .imageio import read_exif
        from .stack import group_frames

        for f in files:
            try:
                exifs[f] = read_exif(f)
            except Exception as exc:  # unreadable EXIF: the frame stands alone
                log.debug("%s: no EXIF for stacking (%s)", f.name, exc)
                exifs[f] = None
        groups = group_frames(files, exifs, stack_cfg.gap_s)
        for g in groups:
            if len(g) > 1:
                log.info("stacking sequence: %s", ", ".join(f.name for f in g))
    done = 0
    for g in groups:
        if len(g) == 1:
            done += 1
            log.info("[%d/%d] %s", done, len(files), g[0].name)
            run_one(g[0])
            continue
        _run_sequence(g, run_one, by_file, rows_by_file, args, overlay_dir, pf, stack_cfg, exifs, done, len(files))
        done += len(g)
    results, rows_by_dir = [], {}
    for f in files:
        results.append(by_file[f])
        od = overlay_dir or default_overlay_dir(f.parent)
        rows_by_dir.setdefault(od, []).extend(rows_by_file[f])
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


def _run_sequence(group, run_one, by_file, rows_by_file, args, overlay_dir, pf, stack_cfg, exifs, done, total):
    """Solve the middle frame; frames that register to it reuse its objects, the rest are solved alone."""
    from .pipeline import process_copy
    from .stack import representative, verify

    rep = representative(group)
    ctx: dict = {}
    log.info("[%d/%d] %s (sequence representative)", done + 1, total, rep.name)
    run_one(rep, ctx)
    for n, f in enumerate([f for f in group if f != rep], start=2):
        log.info("[%d/%d] %s (sequence of %s)", done + n, total, f.name, rep.name)
        m = None
        if by_file[rep]["solved"] and "sol" in ctx:
            try:
                m = verify(f, exifs[f], ctx["sol"], stack_cfg.tol)
            except Exception as exc:
                log.warning("%s: stack check failed (%s); solving it alone", f.name, exc)
        if m is None:
            if by_file[rep]["solved"]:
                log.info("%s: pointing differs from %s; solving it alone", f.name, rep.name)
            run_one(f)
            continue
        by_file[f], rows_by_file[f] = process_copy(f, by_file[rep], ctx["sol"], ctx["ident"], m, args.offset,
                                                   args.time_tolerance, args.site, overlay_dir, not args.no_overlay,
                                                   args.timezone, pf, args.overlay_scores)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
