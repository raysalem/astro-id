"""Annotated overlay JPG: constellation stick figures, object circles and 'name score' labels."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .imageio import load_rgb_preview

COLORS = {"high": (60, 220, 90), "likely": (255, 180, 40), "candidate": (160, 160, 160)}
LINE = (110, 170, 255)
ASTERISM_LINE = (255, 120, 200)
STAR_LABEL_MAG = 3.0  # label named stars only to this magnitude, to keep the frame readable
DSO_KINDS = {"galaxy", "nebula", "cluster", "other"}
LIKELY_MIN = 65


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def draw_overlay(src: Path, out: Path, width_full: int, objects, legend_lines: list[str], width: int = 3000,
                 scores: bool = False, lines: bool = True) -> Path:
    im = load_rgb_preview(src, width)
    s = im.size[0] / width_full
    d = ImageDraw.Draw(im, "RGBA")
    fs = max(14, im.size[0] // 120)
    font, small = _font(fs), _font(int(fs * 0.85))
    lw = max(2, im.size[0] // 1200)

    def lab(o) -> str:  # plain names; the confidence only with --overlay-scores
        return f"{o.name} {o.confidence}" if scores else o.name

    # constellation lines first (under everything)
    for o in objects:
        if o.kind != "constellation" or o.confidence < 10:
            continue
        col = COLORS[o.tier]
        for x1, y1, x2, y2 in (o.figure if lines else ()):
            d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=LINE + (170,), width=lw)
        pts = [(x1 * s, y1 * s) for x1, y1, _, _ in o.figure] + [(x2 * s, y2 * s) for _, _, x2, y2 in o.figure]
        inside = [(px, py) for px, py in pts if 0 <= px < im.size[0] and 0 <= py < im.size[1]]
        if inside:
            cx = sum(p[0] for p in inside) / len(inside)
            cy = sum(p[1] for p in inside) / len(inside)
            _label(d, (cx, cy), (o.name.upper() + (f" {o.confidence}" if scores else "")), font, col, im.size)

    # asterisms: their own stick figure, labelled at the centroid
    for o in objects:
        if getattr(o, "kind", None) != "asterism":
            continue
        for x1, y1, x2, y2 in (o.figure if lines else ()):
            d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=ASTERISM_LINE + (200,), width=lw)
        if o.x is not None:
            _label(d, (o.x * s, o.y * s + fs), lab(o), font, COLORS[o.tier], im.size)

    taken: list[tuple[float, float, float, float]] = []
    for o in sorted(objects, key=lambda o: -o.confidence):
        if o.kind in ("constellation", "asterism") or o.x is None:
            continue
        if getattr(o, "member_of", None) and not getattr(o, "taggable", True):
            # member of a tagged cluster/asterism: small mark, no label (the group carries the name)
            x, y, r = o.x * s, o.y * s, max(3.0, fs * 0.3)
            d.ellipse([x - r, y - r, x + r, y + r], outline=COLORS[o.tier] + (160,), width=max(1, lw - 1))
            continue
        if o.kind == "star" and o.name != "Sun" and (o.mag or 0) > STAR_LABEL_MAG:
            continue
        if o.kind in DSO_KINDS and o.confidence < LIKELY_MIN and (not o.name.startswith("M") or o.confidence < 30):
            continue  # faint NGC/IC and hopeless Messier candidates stay in the JSON/CSV, not on the picture
        col = COLORS[o.tier]
        x, y = o.x * s, o.y * s
        r = max(o.radius_px * s, fs * 0.6)
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (230,), width=lw)
        text = lab(o)
        w = d.textlength(text, font=small)
        for dx, dy in [(r + 4, -fs / 2), (r + 4, fs / 2), (-r - 4 - w, -fs / 2), (-w / 2, r + 2), (-w / 2, -r - fs - 2)]:
            box = (x + dx, y + dy, x + dx + w, y + dy + fs)
            if not any(_overlap(box, t) for t in taken):
                taken.append(box)
                _label(d, (box[0], box[1]), text, small, col, im.size)
                break

    # legend
    pad = fs // 2
    entries = legend_lines + ["", "high >= 80", "likely 65-79", "candidate < 65"]
    lh = int(fs * 1.25)
    box_w = max(d.textlength(t, font=font) for t in entries) + 2 * pad
    box_h = lh * len(entries) + 2 * pad
    y0 = im.size[1] - box_h - pad
    d.rectangle([pad, y0, pad + box_w, y0 + box_h], fill=(0, 0, 0, 170))
    for i, t in enumerate(entries):
        col = (230, 230, 230)
        if t.startswith("high"):
            col = COLORS["high"]
        elif t.startswith("likely"):
            col = COLORS["likely"]
        elif t.startswith("candidate"):
            col = COLORS["candidate"]
        d.text((2 * pad, y0 + pad + i * lh), t, fill=col, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=88)
    return out


def _label(d: ImageDraw.ImageDraw, xy, text: str, font, col, size) -> None:
    x, y = xy
    w = d.textlength(text, font=font)
    x = min(max(2, x), size[0] - w - 2)
    y = min(max(2, y), size[1] - 20)
    d.text((x + 1, y + 1), text, fill=(0, 0, 0, 200), font=font)
    d.text((x, y), text, fill=col, font=font)


def _overlap(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
