"""``astro-id facet sync | serve | status | undo``: the Facet sidecar commands."""
from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger("astro_id.facet")


def _common(p) -> None:
    p.add_argument("--facet-url", help="Facet viewer base URL (default $FACET_URL, else http://127.0.0.1:5000)")
    p.add_argument("--ledger", help="ledger file (default $ASTRO_ID_LEDGER, else the platform data folder)")
    p.add_argument("--path-map", action="append", metavar="FACET=LOCAL",
                   help="map a Facet path prefix to where this machine reads the file (repeatable)")
    p.add_argument("-v", "--verbose", action="store_true")


def _legacy(p) -> None:
    p.add_argument("--legacy-db", metavar="PATH",
                   help="TEMPORARY: write annotations straight into this Facet database when the server has no "
                        "PUT /api/photo/annotations (needs --facet-dir / $FACET_DIR for db/annotations.py)")
    p.add_argument("--facet-dir", help="Facet checkout holding db/annotations.py (only with --legacy-db)")
    p.add_argument("--no-annotations", action="store_true", help="write tags only")


def add_parser(sub) -> None:
    f = sub.add_parser("facet", help="Facet sidecar: solve the library's astro photos over Facet's HTTP API")
    fs = f.add_subparsers(dest="facet_cmd", required=True)

    s = fs.add_parser("sync", help="solve astro photos Facet holds and write tags + annotations back")
    _common(s)
    _legacy(s)
    g = s.add_argument_group("which photos")
    g.add_argument("--path-prefix", help="only photos under this Facet path")
    g.add_argument("--category", default="astro", help="Facet category filter (default astro; '' = any)")
    g.add_argument("--tag", help="only photos carrying this tag")
    g.add_argument("--album", help="only photos in this album id")
    g.add_argument("--since", help="only photos captured on/after YYYY-MM-DD (Facet capture date), or 'last' = "
                                   "the newest capture date the previous sync saw")
    g.add_argument("--force", action="store_true", help="re-solve photos the ledger already holds")
    g.add_argument("--limit", type=int, help="at most this many photos")
    g.add_argument("--spool", help="also take paths from this notifier spool file (one per line; emptied after "
                                   "a successful run)")
    e = s.add_argument_group("solve options (default: each folder's astro-id.json)")
    e.add_argument("--min-confidence", type=int, default=65)
    e.add_argument("--offset")
    e.add_argument("--tolerance-min", type=float)
    e.add_argument("--tz")
    e.add_argument("--source", choices=["jpg", "raw", "auto"])
    e.add_argument("--site", help="lat,lon[,elev] for photos without a position in Facet or astro-id.json")
    e.add_argument("--overlay-dir")
    e.add_argument("--no-overlay", action="store_true")
    e.add_argument("--no-fallback", action="store_true")
    e.add_argument("--stack-mode", choices=["representative", "all"])
    e.add_argument("--engine-flag", action="append", default=[], metavar="FLAG",
                   help="extra `astro-id solve` flag, e.g. --engine-flag=--max-catalog-mag --engine-flag=4.5")
    s.add_argument("--dry-run", action="store_true", help="solve and print what would be written; write nothing")
    s.add_argument("--json", action="store_true", help="JSON lines on stdout (session events + written reports)")

    v = fs.add_parser("serve", help="local web UI for an astro session (127.0.0.1, secret URL)")
    _common(v)
    _legacy(v)
    v.add_argument("--port", type=int, default=0, help="port (default: a free one)")
    v.add_argument("--no-browser", action="store_true", help="do not open the page in a browser")

    a = fs.add_parser("adopt", help="TEMPORARY one-time migration: put the tags/annotations the removed in-process "
                                    "plugin wrote (created_by='astro-id') into the ledger; reads the DB read-only")
    _common(a)
    a.add_argument("--legacy-db", metavar="PATH", required=True, help="the Facet database (opened read-only)")
    a.add_argument("--path-prefix", help="only photos under this path")
    a.add_argument("--dry-run", action="store_true")

    t = fs.add_parser("status", help="what the ledger holds for a Facet library")
    _common(t)

    u = fs.add_parser("undo", help="remove astro-id's tags (and annotations) that the ledger records")
    _common(u)
    _legacy(u)
    u.add_argument("--path-prefix", help="only ledger photos under this path")
    u.add_argument("--dry-run", action="store_true")


def _client(args):
    from .client import FacetClient

    return FacetClient.from_env(args.facet_url)


def _ctx(args, client, ledger, dry_run=False, min_conf=65):
    from .sync import SyncContext

    return SyncContext(client=client, ledger=ledger, dry_run=dry_run, min_confidence=min_conf,
                       legacy_db=getattr(args, "legacy_db", None), facet_dir=getattr(args, "facet_dir", None),
                       annotations=not getattr(args, "no_annotations", False)).prepare()


def run(args) -> int:
    from .client import FacetError
    from .ledger import Ledger, library_key

    try:
        if args.facet_cmd == "serve":
            from .serve import serve

            return serve(args)
        client = _client(args)
        with Ledger(args.ledger) as ledger:
            if args.facet_cmd == "status":
                rows = ledger.rows(library_key(client.url))
                print(json.dumps({"facet": client.url, "ledger": str(ledger.path), "photos": len(rows),
                                  "watermark": ledger.get_meta(library_key(client.url), "since_watermark"),
                                  "rows": rows}, indent=1))
                return 0
            if args.facet_cmd == "adopt":
                from .legacy import adopt_plugin_rows

                print(json.dumps(adopt_plugin_rows(ledger, library_key(client.url), args.legacy_db, args.path_prefix,
                                                   client.server_version(), args.dry_run)))
                return 0
            if args.facet_cmd == "undo":
                from .sync import remove_all

                ctx = _ctx(args, client, ledger, args.dry_run)
                paths = None
                if args.path_prefix:
                    pre = args.path_prefix.replace("\\", "/").rstrip("/").lower() + "/"
                    paths = [r["path"] for r in ledger.rows(ctx.library) if r["path"].replace("\\", "/").lower().startswith(pre)]
                for r in remove_all(ctx, paths):
                    print(json.dumps(r))
                ctx.close()
                return 0
            return cmd_sync(args, client, ledger)
    except (FacetError, ValueError) as exc:
        log.error("%s", exc)
        return 2


def cmd_sync(args, client, ledger) -> int:
    from ..session import SessionOptions
    from .sync import WATERMARK, Selection, parse_path_map, select_candidates, solve_and_write

    path_map = parse_path_map(args.path_map)
    out = (lambda ev: (sys.stdout.write(json.dumps(ev, separators=(",", ":"), default=str) + "\n"),
                       sys.stdout.flush())) if args.json else (lambda ev: None)
    ctx = _ctx(args, client, ledger, args.dry_run, args.min_confidence)
    try:
        sel = Selection(path_prefix=args.path_prefix, category=args.category, tag=args.tag, album_id=args.album,
                        since=args.since, force=args.force, limit=args.limit, spool=args.spool)
        photos, info = select_candidates(client, ledger, ctx.library, sel)
        log.info("Facet %s (version %s): %d listed, %d already synced, %d to solve%s", client.url, ctx.version,
                 info["listed"], info["already_synced"], len(photos), "  [dry run]" if args.dry_run else "")
        out({"event": "sync_start", "facet": client.url, "version": ctx.version, "annotations": ctx.mode,
             "warnings": ctx.warnings, **info, "to_solve": len(photos), "dry_run": args.dry_run})
        if not photos:
            _finish(args, ledger, ctx, info)
            out({"event": "sync_done", "solved": 0, "reports": 0})
            return 0
        opts = SessionOptions(offset=args.offset, tolerance_min=args.tolerance_min, source=args.source,
                              site=args.site, tz=args.tz, min_confidence=args.min_confidence,
                              overlay_dir=args.overlay_dir, no_overlay=args.no_overlay, no_fallback=args.no_fallback,
                              stack_mode=args.stack_mode, extra_flags=list(args.engine_flag))
        opts.validate()

        def emit(ev):
            out(ev)
            if ev.get("event") == "photo":
                log.info("[%s] %s %s (%s objects)", ev.get("index"), ev.get("status"), ev.get("path"),
                         ev.get("objects"))
        res = solve_and_write(ctx, photos, opts, emit, path_map)
        for r in res["reports"]:
            if not args.json:
                print(f"{r['path']}\n   +{r.get('add')}\n   -{r.get('remove')}\n   annotation: {r.get('annotation')}"
                      + (f"\n   error: {r['error']}" if r.get("error") else ""))
        _finish(args, ledger, ctx, info)
        out({"event": "sync_done", "solved": res.get("solved"), "failed": res.get("failed"),
             "reports": len(res["reports"]), "tags_added": sum(len(r.get("add") or []) for r in res["reports"]),
             "tags_removed": sum(len(r.get("remove") or []) for r in res["reports"]),
             "annotations": {k: sum(1 for r in res["reports"] if r.get("annotation") == k)
                             for k in sorted({r.get("annotation") for r in res["reports"] if r.get("annotation")})}})
        return 0
    finally:
        ctx.close()


def _finish(args, ledger, ctx, info) -> None:
    if args.dry_run:
        return
    from .sync import WATERMARK

    if info.get("watermark"):
        prev = ledger.get_meta(ctx.library, WATERMARK)
        if not prev or info["watermark"] > prev:
            ledger.set_meta(ctx.library, WATERMARK, info["watermark"])
    if args.spool:
        try:
            open(args.spool, "w", encoding="utf-8").close()
        except OSError as exc:
            log.warning("could not empty the spool %s: %s", args.spool, exc)
