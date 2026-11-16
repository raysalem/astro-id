"""Annotated overlay JPG: constellation stick figures, object circles and 'name score' labels."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import labels as L
from .imageio import load_rgb_preview

COLORS = {"high": (60, 220, 90), "likely": (255, 180, 40), "candidate": (160, 160, 160)}
LINE = (110, 170, 255)
ASTERISM_LINE = (255, 120, 200)
STAR_LABEL_MAG = 3.0  # label named stars only to this magnitude, to keep the frame readable
DSO_KINDS = {"galaxy", "nebula", "cluster", "other"}
LIKELY_MIN = 65
LEVEL_BODY = 2


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


def ring_obstacle(x, y, r, fs, key=None) -> L.Disc:
    """A drawn marker as a label obstacle: small ones as a disc, big circles only along their ring."""
    return L.Disc(x, y, r + 1, ring=r > 2 * fs, key=key)


def legend_box(d, font, fs, size, entries) -> tuple[float, float, float, float] | None:
    if not entries:
        return None
    pad, lh = fs // 2, int(fs * 1.25)
    bw = max(d.textlength(t, font=font) for t in entries) + 2 * pad
    bh = lh * len(entries) + 2 * pad
    y0 = size[1] - bh - pad
    return (pad, y0, pad + bw, y0 + bh)


def _centroid_inside(pts, size):
    inside = [(px, py) for px, py in pts if 0 <= px < size[0] and 0 <= py < size[1]]
    if not inside:
        return None, []
    return (sum(p[0] for p in inside) / len(inside), sum(p[1] for p in inside) / len(inside)), inside


def layout(objects, s: float, size, fs: int, measure_big, measure_small, scores: bool = False,
           taken: list | None = None, obstacles: list | None = None):
    """What the overlay draws, without drawing: (marks, labels).

    marks: (x, y, r, colour, alpha, width_delta); labels: (Placement, font "big"/"small", colour). Labels are placed
    with :func:`astro_id.labels.place`: bodies, then constellation and asterism names, then the rest by confidence."""
    def lab(o) -> str:  # plain names; the confidence only with --overlay-scores
        return f"{o.name} {o.confidence}" if scores else o.name

    marks = []
    obstacles = [] if obstacles is None else obstacles   # filled in: the drawn markers, for a caller to check
    reqs: list[tuple[tuple, L.Request, str, tuple]] = []
    for n, o in enumerate(objects):
        kind = getattr(o, "kind", None)
        if kind == "constellation":
            if o.confidence < 10:
                continue
            pts = [(x1 * s, y1 * s) for x1, y1, _, _ in o.figure] + [(x2 * s, y2 * s) for _, _, x2, y2 in o.figure]
            c, inside = _centroid_inside(pts, size)
            if c is None:
                continue
            text = o.name.upper() + (f" {o.confidence}" if scores else "")
            reqs.append(((1, -o.confidence, o.name), L.Request(n, text, c[0], c[1], mode="centred",
                                                                slide=sorted(set(inside))), "big", COLORS[o.tier]))
            continue
        if kind == "asterism":
            if o.x is None:
                continue
            pts = [(x1 * s, y1 * s) for x1, y1, _, _ in o.figure] + [(x2 * s, y2 * s) for _, _, x2, y2 in o.figure]
            _, inside = _centroid_inside(pts, size)
            reqs.append(((2, -o.confidence, o.name), L.Request(n, lab(o), o.x * s, o.y * s + fs, mode="centred",
                                                                slide=sorted(set(inside))), "big", COLORS[o.tier]))
            continue
        if o.x is None:
            continue
        x, y = o.x * s, o.y * s
        if getattr(o, "member_of", None) and not getattr(o, "taggable", True):
            # member of a tagged cluster/asterism: small mark, no label (the group carries the name)
            r = max(3.0, fs * 0.3)
            marks.append((x, y, r, COLORS[o.tier], 160, -1))
            obstacles.append(L.Disc(x, y, r + 1, key=n))
            continue
        if kind == "star" and o.name != "Sun" and (o.mag or 0) > STAR_LABEL_MAG:
            continue
        if kind in DSO_KINDS and o.confidence < LIKELY_MIN and (not o.name.startswith("M") or o.confidence < 30):
            continue  # faint NGC/IC and hopeless Messier candidates stay in the JSON/CSV, not on the picture
        r = max(o.radius_px * s, fs * 0.6)
        marks.append((x, y, r, COLORS[o.tier], 230, 0))
        obstacles.append(ring_obstacle(x, y, r, fs, key=n))
        first = 0 if getattr(o, "level", None) == LEVEL_BODY else 3
        reqs.append(((first, -o.confidence, -r, o.name), L.Request(n, lab(o), x, y, r), "small", COLORS[o.tier]))

    reqs.sort(key=lambda t: t[0])
    out = []
    measures = {"big": measure_big, "small": measure_small}
    placed: list = list(taken or [])
    for _, req, font_key, col in reqs:      # one request at a time: each has its own font
        p = L.place([req], size, measures[font_key], obstacles, boxes=placed, fs=fs)[0]
        if p.box is not None:
            placed.append(p.box)
        out.append((p, font_key, col))
    return marks, out


def draw_overlay(src: Path, out: Path, width_full: int, objects, legend_lines: list[str], width: int = 3000,
                 scores: bool = False, lines: bool = True) -> Path:
    im = load_rgb_preview(src, width)
    s = im.size[0] / width_full
    d = ImageDraw.Draw(im, "RGBA")
    fs = max(14, im.size[0] // 120)
    font, small = _font(fs), _font(int(fs * 0.85))
    lw = max(2, im.size[0] // 1200)

    # stick figures first (under everything)
    for o in objects:
        if not lines:
            break
        if o.kind == "constellation" and o.confidence >= 10:
            col = LINE + (170,)
        elif getattr(o, "kind", None) == "asterism":
            col = ASTERISM_LINE + (200,)
        else:
            continue
        for x1, y1, x2, y2 in o.figure:
            d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=col, width=lw)

    entries = legend_lines + ["", "high >= 80", "likely 65-79", "candidate < 65"]
    lbox = legend_box(d, font, fs, im.size, entries)
    marks, labs = layout(objects, s, im.size, fs, L.pil_measure(d, font), L.pil_measure(d, small), scores,
                         taken=[lbox] if lbox else None)
    for x, y, r, col, alpha, dw in marks:
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (alpha,), width=max(1, lw + dw))
    for p, font_key, col in labs:
        if p.box is not None:
            _label(d, (p.box[0], p.box[1]), p.text, font if font_key == "big" else small, col, im.size)

    # legend
    pad, lh = fs // 2, int(fs * 1.25)
    d.rectangle(list(lbox), fill=(0, 0, 0, 170))
    for i, t in enumerate(entries):
        col = (230, 230, 230)
        if t.startswith("high"):
            col = COLORS["high"]
        elif t.startswith("likely"):
            col = COLORS["likely"]
        elif t.startswith("candidate"):
            col = COLORS["candidate"]
        d.text((2 * pad, lbox[1] + pad + i * lh), t, fill=col, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=88)
    return out


def _label(d: ImageDraw.ImageDraw, xy, text: str, font, col, size) -> None:
    """Draw a label whose box :mod:`astro_id.labels` already placed inside the image (top-left at ``xy``)."""
    x, y = xy
    d.text((x + 1, y + 1), text, fill=(0, 0, 0, 200), font=font)
    d.text((x, y), text, fill=col, font=font)


def _overlap(a, b) -> bool:
    return L.overlap(a, b)
