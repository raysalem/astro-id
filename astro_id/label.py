"""`astro-id label <photo-or-folder>`: the simple way in. One command, labeled output, sensible defaults.

Per photo it runs the normal engine (pipeline.process: solve, identify, overlay) and writes, next to the
photo or in --to:
  labeled  <name>_astro.jpg    a COPY with constellation figures and names burned in (original untouched)
  sidecar  <name>.astro.json   that photo's result: objects, confidence, positions, annotations payload
  both     both of the above
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from .pipeline import process

log = logging.getLogger("astro_id")
CONTRACT_VERSION = 1
OUT_CHOICES = ("labeled", "sidecar", "both")
SUFFIX_IMG = "_astro.jpg"
SUFFIX_JSON = ".astro.json"
TIME_NOTE = ("note: time and place only matter for planets and the Moon; stars, constellations and "
             "deep-sky objects are labeled without them (use --offset / --site / astro-id.json if you know them)")


def add_parser(sub) -> None:
    import argparse

    from .vocab import exit_epilog

    s = sub.add_parser("label", help="simplest way in: label a photo or folder (copy with names burned in, "
                                     "and/or an annotations file)",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=exit_epilog("label"))
    s.add_argument("target", help="photo or folder of photos")
    s.add_argument("--out", choices=OUT_CHOICES, default="labeled",
                   help="labeled (default): <name>_astro.jpg copy; sidecar: <name>.astro.json only; both")
    s.add_argument("--to", help="write the results into this folder instead of next to each photo")
    s.add_argument("--recursive", action="store_true", help="also go into sub-folders")
    s.add_argument("--offset", help="camera clock offset, e.g. +2559d11h08m (planets/Moon only)")
    s.add_argument("--site", help="lat,lon[,elev_m] (planets/Moon only)")
    s.add_argument("--time-tolerance", help="time uncertainty, e.g. 15m")
    s.add_argument("--timezone", help="IANA zone of the camera clock")
    s.add_argument("--min-confidence", type=int, default=65)
    s.add_argument("--scores", action="store_true", help="print the confidence after each name on the image")
    s.add_argument("--no-lines", action="store_true", help="names only, no constellation stick figures")
    s.add_argument("--no-fallback", action="store_true", help="never use nova.astrometry.net")
    s.add_argument("-v", "--verbose", action="store_true")


def is_output(p: Path) -> bool:
    return p.stem.endswith("_astro") or p.name.endswith(SUFFIX_JSON)


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".astro-id-", dir=d)
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def _gather(target: Path, recursive: bool) -> list[tuple[Path, Path]]:
    """(photo, folder relative to target) pairs; RAW+JPG pairs count once, our own outputs are skipped."""
    from .imageio import collect

    if target.is_file():
        return [] if is_output(target) else [(f, Path(".")) for f in collect(target)]
    dirs = [(target, Path("."))]
    if recursive:
        dirs += [(d, d.relative_to(target)) for d in sorted(target.rglob("*")) if d.is_dir()]
    out: list[tuple[Path, Path]] = []
    for d, rel in dirs:
        out += [(f, rel) for f in collect(d) if not is_output(f)]
    return out


def _say(msg: str) -> None:
    try:
        sys.stdout.write(msg + "\n")
    except UnicodeEncodeError:   # legacy Windows console codepage: plain ASCII arrows
        sys.stdout.write(msg.replace("→", "->").replace("…", "...").encode("ascii", "replace").decode() + "\n")
    sys.stdout.flush()


def run(args) -> int:
    from . import config as cfgmod
    from .imageio import DIRECT_EXT, RAW_EXT
    from .timeutil import parse_offset, parse_tolerance

    if not args.verbose:
        log.setLevel(logging.ERROR)   # plain progress below; the engine's chatter only with -v
    target = Path(args.target)
    if not target.exists():
        _say(f"not found: {target}")
        return 2
    try:
        if args.offset is not None:
            parse_offset(args.offset)
        if args.time_tolerance is not None:
            parse_tolerance(args.time_tolerance)
        if args.site:
            cfgmod.parse_site(args.site)
    except ValueError as exc:
        _say(f"bad option: {exc}")
        return 2
    from .requirements import RequirementError, require_astap

    try:   # the same gate as `solve`: ASTAP missing or older than the minimum stops here, in one sentence
        require_astap(fallback_ok=not args.no_fallback and bool(os.environ.get("ASTROMETRY_API_KEY")))
    except RequirementError as exc:
        _say(str(exc))
        return 2
    if target.is_file() and target.suffix.lower() not in DIRECT_EXT | RAW_EXT:
        _say(f"not a photo I can read: {target.name}")
        return 2
    items = _gather(target, args.recursive)
    if not items:
        _say(f"no photos found in {target}" + ("" if args.recursive else " (use --recursive for sub-folders)"))
        return 2
    to = Path(args.to) if args.to else None
    want_img = args.out in ("labeled", "both")
    want_json = args.out in ("sidecar", "both")

    labeled = skipped = failed = 0
    dests: list[Path] = []
    noted = False
    for i, (f, rel) in enumerate(items, 1):
        tag = f"{i}/{len(items)} {f.name}"
        dest = (to / rel) if to else f.parent
        if not _writable(dest):
            _say(f"{tag} … cannot write to {dest}; choose a folder you can write to with --to <folder>")
            failed += 1
            continue
        img, js = dest / f"{f.stem}{SUFFIX_IMG}", dest / f"{f.stem}{SUFFIX_JSON}"
        if (not want_img or img.exists()) and (not want_json or js.exists()):
            _say(f"{tag} … skipped, already done")
            skipped += 1
            if dest not in dests:
                dests.append(dest)
            continue
        if not noted:
            noted = True
            try:
                if cfgmod.resolve(f.parent, args.offset, args.time_tolerance, args.site, None, args.timezone).site is None:
                    _say(TIME_NOTE)
            except Exception:
                _say(TIME_NOTE)
        fr, _rows = process(f, args.offset, args.time_tolerance, args.site, dest, want_img, not args.no_fallback,
                            None, args.timezone, None, args.min_confidence, None, args.scores, None, False,
                            overlay_lines=not args.no_lines)
        if not fr["solved"]:
            _say(f"{tag} … not solved: {fr['error'] or 'no plate solution'} (no stars found? check focus/exposure)")
            failed += 1
            continue
        wrote = []
        try:
            if want_json:
                js.write_text(json.dumps({"astro_id_sidecar": 1, "contract": CONTRACT_VERSION, **fr}, indent=1),
                              encoding="utf-8")
                wrote.append(js.name)
            if want_img:
                if not img.exists():
                    raise OSError("labeled image was not written")
                wrote.append(img.name)
        except OSError as exc:
            _say(f"{tag} … solved, but could not write output: {exc}")
            failed += 1
            continue
        _say(f"{tag} … solved, {len(fr['objects'])} objects → {', '.join(wrote)}")
        labeled += 1
        if dest not in dests:
            dests.append(dest)
    where = ", ".join(str(d) for d in dests) or "nowhere"
    extra = f", {skipped} already done" if skipped else ""
    _say(f"Done: {labeled} labeled{extra}, {failed} failed. Files are in {where}")
    return 0 if (labeled + skipped) > 0 else 1
