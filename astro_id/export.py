"""Export: burn the sky labels a panel view shows into a JPG copy, from the stored annotations payload.

No re-solve: everything comes from the payload (ANNOTATIONS.md v1) a solve already produced, filtered exactly as
Facet's photo layer filters it for the same ``annotation_view`` (``settingsFromView`` + ``filterAnnotationItems``
in Facet's ``features/photo-detail/annotations.ts``): group toggles, ``score >= slider`` (None = no score filter),
each metric within its slider by the metric's direction, and members hidden while their group item shows unless
``show_members``. ``visible`` is ignored: exporting is the request to draw.
"""
from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw

from .imageio import load_rgb_preview
from . import labels as L
from .overlay import ASTERISM_LINE, COLORS, LINE, _font, _label, _overlap, legend_box, ring_obstacle  # noqa: F401

LIKELY, HIGH = 65, 80


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def settings(payload: dict, view: dict | None) -> dict:
    """The payload's defaults with the view laid over them, key by key (out-of-range values clamped)."""
    view = view if isinstance(view, dict) else {}
    groups = {g["id"]: g.get("default") is not False for g in payload.get("groups") or [] if isinstance(g, dict)}
    for gid in groups:
        if isinstance((view.get("groups") or {}).get(gid), bool):
            groups[gid] = view["groups"][gid]
    sc = payload.get("score") if isinstance(payload.get("score"), dict) else None
    score = None
    if sc:
        score = sc.get("default") if _num(sc.get("default")) else sc.get("min", 0)
        if "score" in view and view["score"] is None:
            score = None
        elif _num(view.get("score")):
            score = min(sc.get("max", 100), max(sc.get("min", 0), view["score"]))
    metrics = {}
    for m in payload.get("metrics") or []:
        if not isinstance(m, dict) or "key" not in m:
            continue
        v = (view.get("metrics") or {}).get(m["key"])
        metrics[m["key"]] = (min(m.get("max", v), max(m.get("min", v), v)) if _num(v)
                             else m.get("default") if _num(m.get("default")) else m.get("max"))
    return {"groups": groups, "score": score, "metrics": metrics,
            "show_scores": view.get("show_scores") is True, "show_members": view.get("show_members") is True}


def visible_items(payload: dict, s: dict) -> list[dict]:
    """The items Facet would draw for these settings."""
    mdefs = [m for m in payload.get("metrics") or [] if isinstance(m, dict)]
    out = []
    for it in payload.get("items") or []:
        if s["groups"].get(it.get("group")) is False:
            continue
        if s["score"] is not None and _num(it.get("score")) and it["score"] < s["score"]:
            continue
        ok = True
        for m in mdefs:
            v, lim = (it.get("values") or {}).get(m["key"]), s["metrics"].get(m["key"])
            if _num(v) and _num(lim) and (v < lim if m.get("direction") == "min" else v > lim):
                ok = False
                break
        if ok:
            out.append(it)
    if s["show_members"]:
        return out
    shown = {it.get("id") for it in out}
    return [it for it in out if not it.get("member_of") or it["member_of"] not in shown]


def _tier(score) -> str:
    return "high" if (score or 0) >= HIGH else "likely" if (score or 0) >= LIKELY else "candidate"


GROUP_FIRST = {"planet": 0, "constellation": 1, "asterism": 2}


def item_marker_r(it: dict, floor: float) -> float:
    return max(float(it.get("r") or 0), floor)


def lines_anchor(it: dict):
    """(anchor, other points along the figure) for a ``lines`` item, in payload pixels."""
    segs = it.get("segments") or []
    at = it.get("label_at")
    if not at:
        if not segs:
            return None, []
        (x1, y1), (x2, y2) = segs[0]
        at = [(x1 + x2) / 2, (y1 + y2) / 2]
    pts = sorted({(float(p[0]), float(p[1])) for sg in segs for p in sg})
    return (float(at[0]), float(at[1])), pts


def layout_items(items: list[dict], s: float, size, fs: float, measure_big, measure_small, text,
                 marker_floor: float | None = None, taken: list | None = None, obstacles: list | None = None):
    """Marks and placed labels for payload items drawn at scale ``s`` (see :func:`astro_id.overlay.layout`).
    ``text(item, upper)`` gives the label text. Returns (marks [(x, y, r, colour)], [(Placement, font, colour)])."""
    floor = fs * 0.6 if marker_floor is None else marker_floor
    marks, reqs = [], []
    obstacles = [] if obstacles is None else obstacles
    for n, it in enumerate(items):
        col = COLORS[_tier(it.get("score"))]
        sc = -(it.get("score") or 0)
        first = GROUP_FIRST.get(it.get("group"), 3)
        if it.get("shape") == "lines":
            at, pts = lines_anchor(it)
            if at is None:
                continue
            req = L.Request(n, text(it, upper=it.get("group") == "constellation"), at[0] * s, at[1] * s,
                            mode="centred", slide=[(x * s, y * s) for x, y in pts])
            reqs.append(((first, sc, str(it.get("label"))), req, "big", col))
            continue
        if not (_num(it.get("x")) and _num(it.get("y"))):
            continue
        x, y = it["x"] * s, it["y"] * s
        r = max((it.get("r") or 0) * s, floor)
        marks.append((x, y, r, col))
        obstacles.append(ring_obstacle(x, y, r, fs, key=n))
        reqs.append(((first, sc, -r, str(it.get("label"))), L.Request(n, text(it), x, y, r), "small", col))
    reqs.sort(key=lambda t: t[0])
    measures = {"big": measure_big, "small": measure_small}
    placed, out = list(taken or []), []
    for _, req, fk, col in reqs:
        p = L.place([req], size, measures[fk], obstacles, boxes=placed, fs=fs)[0]
        if p.box is not None:
            placed.append(p.box)
        out.append((p, fk, col))
    return marks, out


def render(src: Path, out: Path, payload: dict, view: dict | None = None, *, lines: bool = True,
           scores: bool = False, legend: bool = True, width: int = 3000) -> dict:
    """Draw what ``view`` shows of ``payload`` on a copy of ``src``; returns {"out", "items"}.

    ``lines``: constellation / asterism stick figures; ``scores``: the confidence after each name; ``legend``: an
    info box with the payload's legend line (date, time, solve grade)."""
    im = load_rgb_preview(Path(src), width)
    pw = (payload.get("image") or {}).get("width") or im.size[0]
    s = im.size[0] / float(pw)
    d = ImageDraw.Draw(im, "RGBA")
    fs = max(14, im.size[0] // 120)
    font, small = _font(fs), _font(int(fs * 0.85))
    lw = max(2, im.size[0] // 1200)
    items = visible_items(payload, settings(payload, view))

    def text(it, upper=False) -> str:
        name = str(it.get("label") or it.get("id") or "")
        name = name.upper() if upper else name
        return f"{name} {int(it['score'])}" if scores and _num(it.get("score")) else name

    figures = [it for it in items if it.get("shape") == "lines"]
    for it in figures:                       # stick figures under everything
        col = ASTERISM_LINE if it.get("group") == "asterism" else LINE
        if lines:
            for (x1, y1), (x2, y2) in it.get("segments") or []:
                d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=col + (180,), width=lw)

    entries = []
    if legend:
        entries = [str(payload.get("legend") or "").strip()] if payload.get("legend") else []
        if scores:
            entries += ["high >= 80", "likely 65-79", "candidate < 65"]
    lbox = legend_box(d, font, fs, im.size, entries)
    marks, labs = layout_items(items, s, im.size, fs, L.pil_measure(d, font), L.pil_measure(d, small), text,
                               taken=[lbox] if lbox else None)
    for x, y, r, col in marks:
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (230,), width=lw)
    for p, font_key, col in labs:
        if p.box is not None:
            _label(d, (p.box[0], p.box[1]), p.text, font if font_key == "big" else small, col, im.size)

    if lbox:
        pad, lh = fs // 2, int(fs * 1.25)
        d.rectangle(list(lbox), fill=(0, 0, 0, 170))
        for i, t in enumerate(entries):
            c = COLORS.get(t.split(" ", 1)[0], (230, 230, 230))
            d.text((2 * pad, lbox[1] + pad + i * lh), t, fill=c, font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=90)
    return {"out": str(out).replace("\\", "/"), "items": len(items)}
