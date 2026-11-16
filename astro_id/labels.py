"""Label placement shared by every renderer: the sky overlay, the Export / Moon render and the annotation payload.

Rules (owner's spec): a label never runs past the image edge, never covers another label or another object's
marker; near an edge it goes inward (on the right edge: text, then the circle). Labels are placed one at a time in
the caller's priority order; each tries a fixed list of candidate boxes and takes the first free one. When none is
free a shorter form of the name is tried, then the label is hidden (the marker still draws). Deterministic: the
same requests give the same boxes.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

Box = tuple[float, float, float, float]          # x0, y0, x1, y1


@dataclass
class Request:
    """One label to place.

    ``mode="marker"``: next to the marker at (x, y) with radius r. ``mode="centred"``: centred on (x, y) (a
    constellation name at its figure's centroid), with ``slide`` = other anchors to try along the figure."""
    key: object
    text: str
    x: float
    y: float
    r: float = 0.0
    mode: str = "marker"
    slide: list[tuple[float, float]] = field(default_factory=list)
    short: list[str] = field(default_factory=list)


@dataclass
class Placement:
    key: object
    text: str                 # the text drawn (the short form when ``short``)
    box: Box | None           # the label's box in image pixels; None when hidden
    hidden: bool = False
    short: bool = False

    @property
    def centre(self) -> tuple[float, float] | None:
        if self.box is None:
            return None
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


@dataclass
class Disc:
    """A marker the labels must not cover. ``ring``: only the ring is an obstacle (a big circle)."""
    x: float
    y: float
    r: float
    ring: bool = False
    key: object = None


def overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def hits_disc(b: Box, c: Disc, band: float = 2.0) -> bool:
    nx, ny = min(max(c.x, b[0]), b[2]), min(max(c.y, b[1]), b[3])
    if math.hypot(nx - c.x, ny - c.y) >= c.r:
        return False                                   # box entirely outside the circle
    if not c.ring:
        return True
    far = max(math.hypot(px - c.x, py - c.y) for px in (b[0], b[2]) for py in (b[1], b[3]))
    return far > c.r - band                            # crosses the ring (not wholly inside it)


def inside(b: Box, w: float, h: float, m: float = 1.0) -> bool:
    return b[0] >= m and b[1] >= m and b[2] <= w - m and b[3] <= h - m


_CAT = re.compile(r"^((?:M\s?\d+)|(?:NGC\s?\d+)|(?:IC\s?\d+))\b\s+\S")
_LUNAR = (("Mare ", "M. "), ("Montes ", "Mts "), ("Mons ", "Mt "), ("Sinus ", "S. "), ("Lacus ", "L. "),
          ("Palus ", "P. "), ("Oceanus ", "Oc. "), ("Rupes ", "R. "), ("Vallis ", "V. "))


def short_forms(text: str) -> list[str]:
    """Shorter spellings to try when the full name has no room: a catalog designation alone, an abbreviated
    lunar prefix."""
    out: list[str] = []
    m = _CAT.match(text)
    if m:
        out.append(m.group(1))
    for long, short in _LUNAR:
        if text.startswith(long):
            out.append(short + text[len(long):])
    return [s for s in out if s and s != text]


def _marker_candidates(x, y, r, w, h, fs):
    """Top-left corners: right, left, above, below, diagonals, then pushed outward."""
    g = 0.3 * fs
    d = r * 0.7071
    out = []
    for push in (0.0, 0.6 * fs, 1.2 * fs):
        rr, dd = r + g + push, d + g + push
        out += [(x + rr, y - h / 2), (x - rr - w, y - h / 2),
                (x - w / 2, y - rr - h), (x - w / 2, y + rr),
                (x + dd, y - dd - h), (x + dd, y + dd), (x - dd - w, y - dd - h), (x - dd - w, y + dd)]
    return out


def _centred_candidates(req: Request, w, h, fs):
    anchors = [(req.x, req.y)] + sorted(req.slide, key=lambda p: (math.hypot(p[0] - req.x, p[1] - req.y), p))
    out = []
    for ax, ay in anchors:
        for dx, dy in ((0, 0), (0, -1.1 * h), (0, 1.1 * h), (-0.5 * w, 0), (0.5 * w, 0)):
            out.append((ax + dx - w / 2, ay + dy - h / 2))
    return out


def place(requests: list[Request], size: tuple[float, float], measure, obstacles: list[Disc] = (),
          boxes: list[Box] = (), fs: float | None = None, pad: float = 1.0) -> list[Placement]:
    """Place ``requests`` in order. ``measure(text) -> (width, height)`` of the drawn text (shadow included);
    ``obstacles``: markers (a request's own marker, matched by ``Disc.key == Request.key``, is skipped);
    ``boxes``: areas already taken (a legend). Returns one Placement per request, same order."""
    W, H = size
    taken: list[Box] = list(boxes)
    out: list[Placement] = []
    for req in requests:
        result = Placement(req.key, req.text, None, hidden=True)
        for n, text in enumerate([req.text] + (req.short or short_forms(req.text))):
            tw, th = measure(text)
            f = fs or th
            cands = (_centred_candidates(req, tw, th, f) if req.mode == "centred"
                     else _marker_candidates(req.x, req.y, req.r, tw, th, f))
            for cx, cy in cands:
                if req.mode == "centred":            # a region's name may be nudged in from the edge
                    cx = min(max(cx, pad + 1), W - tw - pad - 1)
                    cy = min(max(cy, pad + 1), H - th - pad - 1)
                b = (cx, cy, cx + tw, cy + th)
                if not inside(b, W, H):
                    continue
                g = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
                if any(overlap(g, t) for t in taken):
                    continue
                if any(o.key != req.key and hits_disc(g, o) for o in obstacles):
                    continue
                result = Placement(req.key, text, b, hidden=False, short=n > 0)
                break
            if not result.hidden:
                break
        if result.box is not None:
            taken.append(result.box)
        out.append(result)
    return out


def pil_measure(draw, font, shadow: int = 1):
    """``measure`` for a PIL font: advance width, and the font's full ascent+descent so every label of one font has
    the same height (plus the 1 px drop shadow)."""
    asc, desc = font.getmetrics() if hasattr(font, "getmetrics") else (font.size, 0)

    def m(text: str) -> tuple[float, float]:
        w = max(draw.textlength(text, font=font), font.getbbox(text)[2] if text else 0)
        return w + shadow, asc + desc + shadow
    return m


def no_overlaps(placements: list[Placement]) -> bool:
    bs = [p.box for p in placements if p.box is not None]
    return not any(overlap(a, b) for i, a in enumerate(bs) for b in bs[i + 1:])
