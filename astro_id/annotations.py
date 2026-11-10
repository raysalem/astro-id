"""Annotations payload v1 (ANNOTATIONS.md): the overlay as data, drawn live by the Facet viewer.

All coordinates are full-resolution source pixels, origin top-left, rounded to 0.1 px. Stick figures are
sampled along the sky great circle and projected through the (SIP-corrected) solution, so they bend with
the lens, then clipped to the frame.
"""
from __future__ import annotations

import json
import math
import re

import numpy as np

from . import tagpolicy as TP

VERSION = 1
MAX_BYTES = 500_000
STAR_MAG_LIMIT = 6.5
PIECE_DEG = 1.5          # max sky length of one sampled piece of a stick-figure segment
MAX_PIECES = 48

GROUPS = [
    {"id": "constellation", "label": "Constellations", "default": True},
    {"id": "planet", "label": "Planets & Moon", "default": True},
    {"id": "catalog", "label": "Messier / NGC / IC", "default": True},
    {"id": "asterism", "label": "Asterisms", "default": True},
    {"id": "star", "label": "Stars", "default": False},
]
METRICS = [{"key": "mag", "label": "Max magnitude", "min": -5, "max": 8, "step": 0.5, "default": 6,
            "direction": "max"}]
SCORE = {"label": "Confidence", "min": 0, "max": 100, "default": 65}
LEVEL_GROUP = {TP.LEVEL_CONSTELLATION: "constellation", TP.LEVEL_BODY: "planet", TP.LEVEL_CATALOG: "catalog",
               TP.LEVEL_ASTERISM: "asterism", TP.LEVEL_STAR: "star"}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")
    return s or "object"


def assign_ids(objs) -> dict[int, str]:
    """Unique slug id per object (keyed by list position)."""
    used: dict[str, int] = {}
    out: dict[int, str] = {}
    for n, o in enumerate(objs):
        base = slug(o.name)
        k = used.get(base, 0) + 1
        used[base] = k
        out[n] = base if k == 1 else f"{base}-{k}"
    return out


def _r(v: float) -> float:
    return round(float(v), 1)


def _clip(x1, y1, x2, y2, w, h):
    """Liang-Barsky clip of a segment to [0, w] x [0, h]; None when fully outside."""
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1), (dx, w - x1), (-dy, y1), (dy, h - y1)):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return None
    return x1 + t0 * dx, y1 + t0 * dy, x1 + t1 * dx, y1 + t1 * dy


def _unit(ra, dec):
    ra, dec = np.radians(ra), np.radians(dec)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def _slerp_points(ra1, dec1, ra2, dec2):
    a, b = _unit(ra1, dec1), _unit(ra2, dec2)
    ang = math.acos(max(-1.0, min(1.0, float(a @ b))))
    if ang < 1e-9:
        pts = np.vstack([a, a])
    else:
        n = int(min(MAX_PIECES, max(1, math.ceil(math.degrees(ang) / PIECE_DEG))))
        t = np.linspace(0.0, 1.0, n + 1)
        pts = (np.sin((1 - t) * ang)[:, None] * a + np.sin(t * ang)[:, None] * b) / math.sin(ang)
    ra = np.degrees(np.arctan2(pts[:, 1], pts[:, 0])) % 360
    dec = np.degrees(np.arcsin(np.clip(pts[:, 2], -1, 1)))
    return ra, dec


def figure_segments(sky_segs, project, width: int, height: int):
    """Sample each sky segment, project it, clip it to the frame.

    ``project(ra, dec) -> (x, y)`` arrays (NaN where untrusted). Returns (segments, vertex pixels in frame)."""
    segs: list[list[list[float]]] = []
    verts: list[tuple[float, float]] = []
    w, h = width - 1, height - 1
    for ra1, dec1, ra2, dec2 in sky_segs:
        ra, dec = _slerp_points(ra1, dec1, ra2, dec2)
        x, y = project(ra, dec)
        for k in (0, -1):
            if np.isfinite(x[k]) and np.isfinite(y[k]) and 0 <= x[k] <= w and 0 <= y[k] <= h:
                verts.append((float(x[k]), float(y[k])))
        for i in range(len(x) - 1):
            if not (np.isfinite(x[i]) and np.isfinite(x[i + 1]) and np.isfinite(y[i]) and np.isfinite(y[i + 1])):
                continue
            c = _clip(float(x[i]), float(y[i]), float(x[i + 1]), float(y[i + 1]), w, h)
            if c is None or math.hypot(c[2] - c[0], c[3] - c[1]) < 0.5:
                continue
            segs.append([[_r(c[0]), _r(c[1])], [_r(c[2]), _r(c[3])]])
    return segs, verts


def legend_text(t_true, tol_min, solver, grade, stars) -> str:
    parts = []
    if t_true is not None:
        z = t_true.strftime("%Z") or ""
        tol = ""
        if tol_min is not None:
            tol = " ±%s min" % (int(tol_min) if float(tol_min).is_integer() else tol_min)
        parts.append(f"{t_true:%Y-%m-%d %H:%M} {z}".rstrip() + tol)
    names = {"astap": "ASTAP", "astrometry.net": "astrometry.net", "stack-copy": "stack-copy"}
    s = names.get(solver or "", solver or "unsolved")
    if grade:
        s += f" grade {grade}"
    parts.append(s)
    if stars:
        parts.append(f"{int(stars)} stars")
    return " · ".join(parts)


def build_annotations(objs, sol, width: int, height: int, legend: str, xform=None) -> dict:
    """The v1 payload for one frame. ``sol`` projects sky -> pixels; ``xform(x, y)`` (optional) then maps
    those pixels into this frame (a stacking sequence frame reuses the representative's solution)."""
    from .solve import safe_world2pix

    def project(ra, dec):
        x, y = safe_world2pix(sol.wcs, ra, dec, sol.width, sol.height)
        if xform is not None:
            ok = np.isfinite(x) & np.isfinite(y)
            xs, ys = np.full(x.shape, np.nan), np.full(y.shape, np.nan)
            if ok.any():
                xs[ok], ys[ok] = xform(x[ok], y[ok])
            return xs, ys
        return x, y

    ids = assign_ids(objs)
    tag_to_id = {o.tag: ids[n] for n, o in enumerate(objs)}
    items: list[dict] = []
    for n, o in enumerate(objs):
        group = LEVEL_GROUP.get(o.level)
        if group is None:
            continue
        it = {"id": ids[n], "label": o.name, "group": group, "score": int(o.confidence)}
        if group in ("constellation", "asterism"):
            segs, verts = figure_segments(o.figure_sky, project, width, height)
            if not segs:
                continue
            pts = verts or [p for s in segs for p in s]
            it["shape"] = "lines"
            it["segments"] = segs
            it["label_at"] = [_r(sum(p[0] for p in pts) / len(pts)), _r(sum(p[1] for p in pts) / len(pts))]
        else:
            if o.x is None or o.y is None or not (0 <= o.x <= width - 1 and 0 <= o.y <= height - 1):
                continue
            if o.kind == "star" and o.mag is not None and o.mag > STAR_MAG_LIMIT:
                continue
            it["x"], it["y"] = _r(o.x), _r(o.y)
            if group == "catalog":
                it["shape"], it["r"] = "circle", _r(max(o.radius_px, 12.0))
            else:
                it["shape"] = "point"
                it["r"] = _r(max(o.radius_px, 18.0 if group == "planet" else 10.0))
            if o.mag is not None:
                it["values"] = {"mag": round(float(o.mag), 2)}
            mo = tag_to_id.get(o.member_of) if o.member_of else None
            if mo and mo != it["id"]:
                it["member_of"] = mo
        items.append(it)
    payload = {"version": VERSION, "source": "astro-id", "title": "Sky annotations",
               "image": {"width": int(width), "height": int(height)}, "legend": legend,
               "groups": [dict(g) for g in GROUPS], "metrics": [dict(m) for m in METRICS],
               "score": dict(SCORE), "items": items}
    return cap_size(payload)


def encoded_size(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def cap_size(payload: dict, limit: int = MAX_BYTES) -> dict:
    """Keep the payload under ``limit`` bytes: drop the lowest-score items first (never the planets)."""
    if encoded_size(payload) <= limit:
        return payload
    items = payload["items"]
    order = sorted(range(len(items)), key=lambda i: (items[i]["group"] == "planet", items[i]["score"],
                                                     -items[i].get("values", {}).get("mag", 0)))
    dropped: set[int] = set()
    for i in order:
        dropped.add(i)
        payload["items"] = [it for k, it in enumerate(items) if k not in dropped]
        if encoded_size(payload) <= limit:
            break
    keep = {it["id"] for it in payload["items"]}
    for it in payload["items"]:
        if it.get("member_of") and it["member_of"] not in keep:
            del it["member_of"]
    return payload
