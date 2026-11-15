"""Rebuild the example set in examples/ and the README images in docs/img/.

The source photos are not in the repo. Steps, from the repo root:

    uv run python tools/make_examples.py prep <source-folder>      # downscale + strip EXIF -> examples/*.jpg
    uv run astro-id solve examples/ --json --no-fallback --overlay-dir examples/annotated > solve.json
    uv run python tools/make_examples.py expected solve.json        # -> examples/expected.json
    uv run python tools/make_examples.py composites                 # -> docs/img/hero.jpg, gallery-1..3.jpg

prep keeps only the EXIF astro-id needs (DateTimeOriginal, FocalLength, FNumber, ExposureTime, ISO, Make, Model,
LensModel, Orientation): no serial numbers, owner, GPS, maker notes or thumbnail.
"""
import json
import os
import sys
from pathlib import Path

from PIL import ExifTags, Image

LONG_EDGE = 2400
QUALITY = 88
# source file name -> example name
PICKS = {
    "DSC07610.JPG": "01-cassiopeia-perseus",
    "DSC07611.JPG": "02-milky-way-cepheus",
    "DSC07620.JPG": "03-dawn-orion-sirius",
    "DSC07616.JPG": "04-orion-rising",
    "DSC07625.JPG": "05-dawn-jupiter-mars",
}
HERO = "04-orion-rising"
# the README strip under the hero: docs/img/gallery-1..3.jpg (frame 02 is linked from the README instead)
GALLERY = ["01-cassiopeia-perseus", "03-dawn-orion-sirius", "05-dawn-jupiter-mars"]
IFD0_KEEP = (0x010F, 0x0110, 0x0112)                       # Make, Model, Orientation
EXIF_KEEP = (0x9003, 0x920A, 0x829D, 0x829A, 0x8827, 0xA434)  # DateTimeOriginal, FocalLength, FNumber,
                                                           # ExposureTime, ISOSpeedRatings, LensModel
ASSERT_MIN, PLANET_MIN, LISTED_MIN = 85, 80, 80
BODY_TYPES = {"planet", "moon", "sun"}


def prep_one(src, dst, long_edge=LONG_EDGE, quality=QUALITY):
    with Image.open(src) as im:
        ex = im.getexif()
        sub = ex.get_ifd(ExifTags.IFD.Exif)
        w, h = im.size
        s = long_edge / max(w, h)
        im.draft("RGB", (int(w * s * 1.01) + 1, int(h * s * 1.01) + 1))
        small = im.convert("RGB").resize((round(w * s), round(h * s)), Image.LANCZOS)
    new = Image.Exif()
    for t in IFD0_KEEP:
        if t in ex:
            new[t] = ex[t]
    nsub = new.get_ifd(ExifTags.IFD.Exif)
    for t in EXIF_KEEP:
        if t in sub:
            nsub[t] = sub[t]
    small.save(dst, "JPEG", quality=quality, optimize=True, exif=new.tobytes())
    print(dst, small.size, os.path.getsize(dst) // 1024, "KB")


def prep(src_dir, out_dir="examples"):
    for src, name in PICKS.items():
        prep_one(Path(src_dir) / src, Path(out_dir) / f"{name}.jpg")


def _is_body(o):
    return str(o.get("kind", "")).lower() in BODY_TYPES


def expected(solve_json, out="examples/expected.json"):
    d = json.loads(Path(solve_json).read_text(encoding="utf-8"))
    res = {"generated_by": "uv run astro-id solve examples/ --json --no-fallback --overlay-dir examples/annotated",
           "astro_id_contract": d.get("contract"), "min_confidence": LISTED_MIN,
           "note": ("tags: taggable objects (default tag policy) at confidence >= 85, plus planets/Moon/Sun at >= 80 "
                    "(a grade-C solve caps a planet near 82); assert these. borderline: other tags at 80-84, "
                    "informational only, may move across the 80 line with a different ASTAP or catalog build. "
                    "scores: the confidence each listed tag had in this run (informational)."),
           "files": {}}
    for r in d["results"]:
        best = {}
        for o in r.get("objects") or []:
            if o.get("taggable") and o["confidence"] >= LISTED_MIN:
                tag = o["tag"]
                if o["confidence"] > best.get(tag, (-1, None))[0]:
                    best[tag] = (o["confidence"], o)
        robust = sorted(t for t, (c, o) in best.items() if c >= ASSERT_MIN or (_is_body(o) and c >= PLANET_MIN))
        border = sorted(set(best) - set(robust))
        res["files"][Path(r["file"]).name] = {
            "solved": r["solved"], "grade": r["grade"], "tags": robust, "borderline": border,
            "scores": {t: best[t][0] for t in sorted(best)}}
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=2)
        f.write("\n")
    print(out)


def _pair(orig, ann, dst, width, max_kb):
    gap = 6
    half = (width - gap) // 2
    a = Image.open(orig).convert("RGB")
    b = Image.open(ann).convert("RGB")
    h = round(a.height * half / a.width)
    canvas = Image.new("RGB", (half * 2 + gap, h), (16, 16, 16))
    canvas.paste(a.resize((half, h), Image.LANCZOS), (0, 0))
    canvas.paste(b.resize((half, h), Image.LANCZOS), (half + gap, 0))
    for q in (88, 85, 82, 78, 74, 70, 65):
        canvas.save(dst, "JPEG", quality=q, optimize=True)
        if os.path.getsize(dst) < max_kb * 1024:
            break
    print(dst, canvas.size, os.path.getsize(dst) // 1024, "KB q", q)


def composites(ex="examples", img="docs/img"):
    ann = f"{ex}/annotated"
    _pair(f"{ex}/{HERO}.jpg", f"{ann}/{HERO}_astro.jpg", f"{img}/hero.jpg", 1600, 490)
    for i, n in enumerate(GALLERY, 1):
        _pair(f"{ex}/{n}.jpg", f"{ann}/{n}_astro.jpg", f"{img}/gallery-{i}.jpg", 900, 195)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prep" and len(sys.argv) == 3:
        prep(sys.argv[2])
    elif cmd == "expected" and len(sys.argv) == 3:
        expected(sys.argv[2])
    elif cmd == "composites":
        composites()
    else:
        sys.exit(__doc__)
