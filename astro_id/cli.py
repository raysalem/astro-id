"""Command line: `astro-id solve|site|setup|session|cull` (contract v1.1, see CONTRACT.md) and `astro-id facet ...`."""
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
    s.add_argument("--timezone", help="IANA zone of the camera-local time (EXIF + offset) and of a naive --time "
                                      "(default: astro-id.json timezone, else inferred from the site, else system)")
    s.add_argument("--overlay-dir", help="where overlays and the CSV report go (default: <folder>-annotated)")
    s.add_argument("--no-overlay-lines", action="store_true", help="overlay JPG without constellation/asterism lines")
    s.add_argument("--overlay-scores", action="store_true",
                   help="print the confidence after each name on the overlay JPG (default: names only)")
    s.add_argument("--solve-source", choices=["jpg", "raw", "auto"],
                   help="pixels the solver reads: jpg (default), raw (the same-stem RAW, cropped to the JPG frame), "
                        "auto (raw when a RAW twin exists, else jpg); results stay in JPG pixel space")
    s.add_argument("--json", action="store_true", help="print the contract JSON to stdout")
    s.add_argument("--no-overlay", action="store_true")
    s.add_argument("--no-fallback", action="store_true", help="never use nova.astrometry.net")
    s.add_argument("--no-quality", action="store_true", help="skip the per-frame quality metrics (focus, trailing...)")
    g = s.add_argument_group("tag policy (default: astro-id.json tag_policy, else levels 1-4, stars off)")
    g.add_argument("--tag-levels", help="levels that become object tags, e.g. 1,2,3,4 (1 constellation, 2 Sun/Moon/planet, "
                                        "3 Messier/NGC/IC, 4 asterism, 5 named star)")
    g.add_argument("--star-mag-limit", type=float,
                   help="give named stars at least this bright an object tag (e.g. 1.5 = the ~20 brightest); turns level 5 on")
    g.add_argument("--max-catalog-mag", type=float, help="faintest catalog object (level 3) that gets an object tag (default 6.0)")
    g.add_argument("--tag-style", choices=["plain", "kind"],
                   help="plain: 'orion'; kind: 'constellation/orion', 'planet/jupiter' (default plain)")
    k = s.add_argument_group("stacking sequences (folder runs)")
    k.add_argument("--stack-mode", choices=["representative", "all"],
                   help="representative: solve the middle frame of a sequence and reuse it for frames that "
                        "point the same way (default); all: solve every frame")
    k.add_argument("--stack-gap", type=float, help="max seconds between frames of one sequence (default 120)")
    s.add_argument("-v", "--verbose", action="store_true")
    _add_session(sub)
    from .facet.cli import add_parser as _add_facet

    _add_facet(sub)
    t = sub.add_parser("site", help="pick the observing site on Google Maps -> astro-id.json")
    t.add_argument("folder")
    d = sub.add_parser("setup", help="download reference data and check for ASTAP")
    d.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.argv = list(argv) if argv is not None else sys.argv[1:]   # the add-on keeper starts its worker with these
    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(message)s")
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.cmd == "site":
        from .site import run_site

        return run_site(Path(args.folder))
    if args.cmd == "setup":
        return cmd_setup()
    if args.cmd == "session":
        return cmd_session(args)
    if args.cmd == "cull":
        return cmd_cull(args)
    if args.cmd == "facet":
        from .facet.cli import run as run_facet

        return run_facet(args)
    return cmd_solve(args)


def _add_session(sub) -> None:
    s = sub.add_parser("session", help="astro session: preview the time/site per photo, or solve a set of photos "
                                       "with the session's choices; JSON lines on stdout")
    s.add_argument("paths", nargs="*", help="photo files (or use --paths-file)")
    s.add_argument("--paths-file", help='JSON: a list of paths, or {"paths": [...], "hints": {path: {"site", "time"}}}')
    s.add_argument("--hints-file", help='JSON {path: {"site": "lat,lon[,elev]", "time": ISO}}: per-photo facts '
                                        "(a position or corrected capture time the photo library holds)")
    s.add_argument("--preview", action="store_true",
                   help="do not solve: per photo EXIF time, corrected time, window, site and timezone source, RAW twin")
    s.add_argument("--offset", help="camera clock offset, e.g. +2559d11h08m (overrides astro-id.json)")
    s.add_argument("--tolerance-min", type=float, help="time tolerance in minutes, e.g. 5 (overrides astro-id.json)")
    s.add_argument("--source", choices=["jpg", "raw", "auto"], help="pixels the solver reads (default astro-id.json/jpg)")
    s.add_argument("--site", help="lat,lon[,elev_m] for photos without their own position")
    s.add_argument("--tz", help="IANA timezone of the camera-local time, e.g. America/Los_Angeles")
    s.add_argument("--min-confidence", type=int, default=65)
    s.add_argument("--save-folder-config", action="store_true",
                   help="write offset/tolerance/timezone into each photo folder's astro-id.json first")
    s.add_argument("--cull-rule", help="cull rule as JSON text or @file (default astro-id.json \"cull\", else built-in)")
    s.add_argument("--results", action="store_true", help="include the full frame result in each photo event")
    s.add_argument("--overlay-dir")
    s.add_argument("--no-overlay", action="store_true")
    s.add_argument("--no-fallback", action="store_true")
    s.add_argument("--no-quality", action="store_true")
    s.add_argument("--no-overlay-lines", action="store_true", help="overlay JPG without constellation/asterism lines")
    s.add_argument("--overlay-scores", action="store_true")
    s.add_argument("--stack-mode", choices=["representative", "all"])
    s.add_argument("--stack-gap", type=float)
    s.add_argument("-v", "--verbose", action="store_true")
    c = sub.add_parser("cull", help="re-run only the culling on stored quality metrics; one JSON document on stdout")
    c.add_argument("--metrics-file", required=True,
                   help='JSON {path: quality} or {"metrics": {path: quality}, "solved": {path: bool}}')
    c.add_argument("--cull-rule", help="cull rule as JSON text or @file")
    c.add_argument("-v", "--verbose", action="store_true")


def _json_arg(text: str | None):
    if text is None:
        return None
    if text.startswith("@"):
        return json.loads(Path(text[1:]).read_text(encoding="utf-8"))
    return json.loads(text)


def _emit(ev: dict) -> None:
    sys.stdout.write(json.dumps(ev, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def cmd_session(args) -> int:
    from .session import SessionOptions, run_session, save_folder_config, time_preview

    try:
        paths = list(args.paths)
        hints: dict = {}
        if args.paths_file:
            doc = json.loads(Path(args.paths_file).read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                paths += list(doc.get("paths") or [])
                hints.update(doc.get("hints") or {})
            else:
                paths += list(doc)
        if args.hints_file:
            hints.update(json.loads(Path(args.hints_file).read_text(encoding="utf-8")))
        if not paths:
            raise ValueError("no photos: give paths or --paths-file")
        opts = SessionOptions(offset=args.offset, tolerance_min=args.tolerance_min, source=args.source,
                              site=args.site, tz=args.tz, min_confidence=args.min_confidence, hints=hints,
                              cull=_json_arg(args.cull_rule), overlay_dir=args.overlay_dir, no_overlay=args.no_overlay,
                              no_fallback=args.no_fallback, no_quality=args.no_quality,
                              overlay_scores=args.overlay_scores, overlay_lines=not args.no_overlay_lines, stack_mode=args.stack_mode,
                              stack_gap=args.stack_gap, include_results=args.results)
        opts.validate()
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        _emit({"event": "error", "error": str(exc)})
        return 2
    if args.preview:
        if args.save_folder_config:
            for w in save_folder_config([str(Path(p).parent) for p in paths], opts):
                _emit({"event": "config_saved", "path": w})
        time_preview(paths, opts, _emit)
        return 0
    run_session(paths, opts, _emit, save_config=args.save_folder_config)
    return 0


def cmd_cull(args) -> int:
    from .cull import validate_rule
    from .session import recull

    try:
        doc = json.loads(Path(args.metrics_file).read_text(encoding="utf-8"))
        rule = _json_arg(args.cull_rule)
        errs = validate_rule(rule)
        if errs:
            raise ValueError("; ".join(errs))
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        return 2
    metrics = doc.get("metrics", doc) if isinstance(doc, dict) else {}
    solved = doc.get("solved") if isinstance(doc, dict) and "metrics" in doc else None
    sys.stdout.write(json.dumps(recull(metrics, rule, solved), indent=1) + "\n")
    return 0


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
    results = solve_files(files, args, stack_cfg)
    if args.json:
        sys.stdout.write(json.dumps({"contract": CONTRACT_VERSION, "results": results}, indent=1) + "\n")
    return 0


def solve_files(files: list[Path], args, stack_cfg, on_frame=None) -> list[dict]:
    """Solve ``files`` with the parsed ``solve`` flags ``args``; returns the frame results in ``files`` order.

    ``on_frame(result)`` (optional) is called as each frame finishes (sequence representatives first)."""
    from .pipeline import CSV_NAME, default_overlay_dir, process, write_csv

    overlay_dir = Path(args.overlay_dir) if args.overlay_dir else None
    pf = policy_flags(args)
    by_file: dict = {}
    rows_by_file: dict = {}

    def run_one(f, ctx=None):
        fr, rows = process(f, args.offset, args.time_tolerance, args.site, overlay_dir, not args.no_overlay,
                           not args.no_fallback, args.time, args.timezone, pf, args.min_confidence, ctx,
                           args.overlay_scores, args.solve_source, not getattr(args, "no_quality", False),
                           overlay_lines=not getattr(args, "no_overlay_lines", False))
        by_file[f], rows_by_file[f] = fr, rows
        if on_frame is not None:
            on_frame(fr)

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
        _run_sequence(g, run_one, by_file, rows_by_file, args, overlay_dir, pf, stack_cfg, exifs, done, len(files),
                      on_frame)
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
    return results


def _run_sequence(group, run_one, by_file, rows_by_file, args, overlay_dir, pf, stack_cfg, exifs, done, total,
                  on_frame=None):
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
                                                   args.timezone, pf, args.overlay_scores,
                                                   not getattr(args, "no_quality", False),
                                                   overlay_lines=not getattr(args, "no_overlay_lines", False))
        if on_frame is not None:
            on_frame(by_file[f])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
