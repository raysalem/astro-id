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
from .overlay import ASTERISM_LINE, COLORS, LINE, _font, _label, _overlap

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

    taken: list[tuple[float, float, float, float]] = []
    figures = [it for it in items if it.get("shape") == "lines"]
    for it in figures:                       # stick figures under everything
        col = ASTERISM_LINE if it.get("group") == "asterism" else LINE
        if lines:
            for (x1, y1), (x2, y2) in it.get("segments") or []:
                d.line([(x1 * s, y1 * s), (x2 * s, y2 * s)], fill=col + (180,), width=lw)
    for it in figures:
        at = it.get("label_at")
        if not at:
            segs = it.get("segments") or []
            if not segs:
                continue
            (x1, y1), (x2, y2) = segs[0]
            at = [(x1 + x2) / 2, (y1 + y2) / 2]
        t = text(it, upper=it.get("group") == "constellation")
        w = d.textlength(t, font=font)
        box = (at[0] * s - w / 2, at[1] * s - fs / 2, at[0] * s + w / 2, at[1] * s + fs / 2)
        taken.append(box)
        _label(d, (box[0], box[1]), t, font, COLORS[_tier(it.get("score"))], im.size)

    for it in sorted((i for i in items if i.get("shape") != "lines"), key=lambda i: -(i.get("score") or 0)):
        if not (_num(it.get("x")) and _num(it.get("y"))):
            continue
        col = COLORS[_tier(it.get("score"))]
        x, y = it["x"] * s, it["y"] * s
        r = max((it.get("r") or 0) * s, fs * 0.6)
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (230,), width=lw)
        t = text(it)
        w = d.textlength(t, font=small)
        for dx, dy in [(r + 4, -fs / 2), (r + 4, fs / 2), (-r - 4 - w, -fs / 2), (-w / 2, r + 2), (-w / 2, -r - fs - 2)]:
            box = (x + dx, y + dy, x + dx + w, y + dy + fs)
            if not any(_overlap(box, b) for b in taken):
                taken.append(box)
                _label(d, (box[0], box[1]), t, small, col, im.size)
                break

    if legend:
        entries = [str(payload.get("legend") or "").strip()] if payload.get("legend") else []
        if scores:
            entries += ["high >= 80", "likely 65-79", "candidate < 65"]
        if entries:
            pad, lh = fs // 2, int(fs * 1.25)
            bw = max(d.textlength(t, font=font) for t in entries) + 2 * pad
            bh = lh * len(entries) + 2 * pad
            y0 = im.size[1] - bh - pad
            d.rectangle([pad, y0, pad + bw, y0 + bh], fill=(0, 0, 0, 170))
            for i, t in enumerate(entries):
                c = COLORS.get(t.split(" ", 1)[0], (230, 230, 230))
                d.text((2 * pad, y0 + pad + i * lh), t, fill=c, font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=90)
    return {"out": str(out).replace("\\", "/"), "items": len(items)}
