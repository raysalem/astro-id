"""Stacking sequences: consecutive frames of the same setup share one plate solution.

A sequence is frames taken back to back (gap <= stack_gap_s) with the same lens, focal length,
exposure and image size. The middle frame is solved in full; every other frame is registered to it
with a cheap star-pattern check (detection + affine fit, no ASTAP). When the pointing moved less
than the tolerance, the frame reuses the representative's objects (positions mapped through the
fit) and is reported with ``solver: "stack-copy"`` and ``stack_of``. Otherwise it is solved on its own.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

log = logging.getLogger("astro_id")

MIN_PAIRS = 15
MIN_BRIGHT_FRACTION = 0.6
MAX_ROTATION_DEG = 3.0
MAX_SCALE_CHANGE = 0.02


def _setup_key(exif) -> tuple:
    exp = None if exif.exposure_s is None else round(float(exif.exposure_s), 4)
    foc = None if exif.focal_mm is None else round(float(exif.focal_mm), 1)
    return (exif.lens, foc, exp, exif.width, exif.height)


def group_frames(files: list[Path], exifs: dict, gap_s: float) -> list[list[Path]]:
    """Sequences of consecutive frames (time gap <= gap_s, same setup). Frames without a time stand alone.

    Returned in the order of their first frame in ``files``."""
    timed = sorted((f for f in files if exifs.get(f) is not None and exifs[f].datetime_original is not None),
                   key=lambda f: exifs[f].datetime_original)
    groups: list[list[Path]] = []
    for f in timed:
        if groups:
            prev = groups[-1][-1]
            dt = (exifs[f].datetime_original - exifs[prev].datetime_original).total_seconds()
            if 0 <= dt <= gap_s and _setup_key(exifs[f]) == _setup_key(exifs[prev]):
                groups[-1].append(f)
                continue
        groups.append([f])
    seen = {f for g in groups for f in g}
    groups += [[f] for f in files if f not in seen]
    pos = {f: i for i, f in enumerate(files)}
    return sorted(groups, key=lambda g: min(pos[f] for f in g))


def representative(group: list[Path]) -> Path:
    return group[len(group) // 2]


@dataclass
class StackMatch:
    M: np.ndarray  # 3x2 affine, [x y 1] @ M: representative pixel -> this frame's pixel
    offset_px: float
    offset_deg: float
    rotation_deg: float
    scale: float
    pairs: int
    bright_fraction: float
    rms_px: float
    smooth: float

    def apply(self, x, y):
        p = np.c_[np.atleast_1d(x), np.atleast_1d(y), np.ones(np.size(x))] @ self.M
        return p[:, 0], p[:, 1]

    def inverse(self, x, y):
        A, t = self.M[:2].T, self.M[2]
        q = np.linalg.solve(A, (np.c_[np.atleast_1d(x), np.atleast_1d(y)] - t).T).T
        return q[:, 0], q[:, 1]


def _affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    X = np.c_[src, np.ones(len(src))]
    M, *_ = np.linalg.lstsq(X, dst, rcond=None)
    return M


def register(rep_xy: np.ndarray, rep_bright: np.ndarray, det_xy: np.ndarray, det_bright: np.ndarray,
             width: int, height: int, psf: float, max_shift_px: float) -> StackMatch | None:
    """Affine registration of a frame's detections to the representative's.

    rep_xy/det_xy: all detections (full-res); *_bright: the brightest ~200 of each, for the shift vote."""
    if len(rep_bright) < 5 or len(det_bright) < 5:
        return None
    lim = max_shift_px * 1.5 + 20
    dx = det_bright[None, :, 0] - rep_bright[:, None, 0]
    dy = det_bright[None, :, 1] - rep_bright[:, None, 1]
    ok = (np.abs(dx) <= lim) & (np.abs(dy) <= lim)
    if ok.sum() < 5:
        return None
    b = max(6.0, 2.0 * psf)
    nb = max(2, int(math.ceil(2 * lim / b)))
    H, xe, ye = np.histogram2d(dx[ok], dy[ok], bins=nb, range=[[-lim, lim], [-lim, lim]])
    i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    if H[i, j] < 5:
        return None
    cx, cy = (xe[i] + xe[i + 1]) / 2, (ye[j] + ye[j + 1]) / 2
    near = ok & (np.abs(dx - cx) <= b) & (np.abs(dy - cy) <= b)
    shift = np.array([np.median(dx[near]), np.median(dy[near])])
    M = np.array([[1.0, 0.0], [0.0, 1.0], shift])
    tree = cKDTree(det_xy)
    pairs = None
    for r in (max(4 * psf, 25.0), max(3 * psf, 12.0), max(2 * psf, 6.0)):
        pred = np.c_[rep_xy, np.ones(len(rep_xy))] @ M
        d, k = tree.query(pred, distance_upper_bound=r)
        good = np.isfinite(d)
        if good.sum() < MIN_PAIRS:
            return None
        src, dst = rep_xy[good], det_xy[k[good]]
        M = _affine(src, dst)
        pairs = (src, dst)
    src, dst = pairs
    res = np.hypot(*(np.c_[src, np.ones(len(src))] @ M - dst).T)
    A = M[:2].T
    scale = float(math.sqrt(abs(np.linalg.det(A))))
    rot = float(math.degrees(math.atan2(A[1, 0] - A[0, 1], A[0, 0] + A[1, 1])))
    c = np.array([(width - 1) / 2, (height - 1) / 2])
    off = float(np.hypot(*(np.r_[c, 1.0] @ M - c)))
    # share of this frame's brightest detections that have a counterpart in the representative
    btree = cKDTree(rep_xy @ M[:2] + M[2])
    db, _ = btree.query(det_bright, distance_upper_bound=max(3 * psf, 8.0))
    frac = float(np.mean(np.isfinite(db)))
    return StackMatch(M, off, float("nan"), rot, scale, len(src), frac, float(np.sqrt(np.mean(res ** 2))), 0.0)


def verify(path: Path, exif, rep_sol, tol: float) -> StackMatch | None:
    """Register ``path`` to the representative's solution; None when the pointing does not match."""
    from .imageio import load_gray
    from .solve import SMOOTH_SCALES, reduce_for
    from .stars import detect

    if (exif.width, exif.height) != (rep_sol.width, rep_sol.height) or rep_sol.det is None:
        return None
    w, h = rep_sol.width, rep_sol.height
    max_shift = tol * math.hypot(w, h)
    rd = rep_sol.det
    rep_xy = np.c_[rd.x, rd.y]
    rep_bright = rep_xy[rd.order_by_peak()[:200]]
    gray = load_gray(path, reduce_for(w, h))  # same scale as the solve, so det_smooth means the same
    R = w / gray.shape[1]
    cell = max(32, gray.shape[1] // 75)
    scales = [rep_sol.det_smooth] + [s for s in SMOOTH_SCALES if s != rep_sol.det_smooth]
    best = None
    for sm in scales:
        d = detect(gray, sigma=5.0, smooth=sm, cell=max(cell, int(10 * sm))).scaled(R)
        if len(d) < 8:
            continue
        det_xy = np.c_[d.x, d.y]
        m = register(rep_xy, rep_bright, det_xy, det_xy[d.order_by_peak()[:200]], w, h,
                     max(rep_sol.psf_px, 2.0), max_shift)
        if m is None:
            continue
        m.smooth = sm
        if best is None or m.bright_fraction > best.bright_fraction:
            best = m
        if _accept(m, max_shift):
            break
    if best is None:
        return None
    sc = _center_scale_deg(rep_sol)
    best.offset_deg = best.offset_px * sc
    log.info("  stack check %s: shift %.1f px (%.2f deg), rot %.2f deg, %d pairs, bright match %.0f%%, rms %.1f px",
             path.name, best.offset_px, best.offset_deg, best.rotation_deg, best.pairs, 100 * best.bright_fraction,
             best.rms_px)
    return best if _accept(best, max_shift) else None


def _accept(m: StackMatch, max_shift: float) -> bool:
    return (m.pairs >= MIN_PAIRS and m.bright_fraction >= MIN_BRIGHT_FRACTION and m.offset_px <= max_shift
            and abs(m.rotation_deg) <= MAX_ROTATION_DEG and abs(m.scale - 1) <= MAX_SCALE_CHANGE)


def _center_scale_deg(sol) -> float:
    a = sol.wcs.pixel_to_world([sol.width / 2, sol.width / 2 + 10], [sol.height / 2] * 2)
    return float(a[0].separation(a[1]).deg) / 10.0


def map_objects(objs, m: StackMatch, width: int, height: int):
    """The representative's objects moved into this frame; point objects that leave the frame are dropped."""
    import dataclasses

    out = []
    for o in objs:
        fig = []
        for x1, y1, x2, y2 in o.figure:
            xs, ys = m.apply([x1, x2], [y1, y2])
            fig.append((float(xs[0]), float(ys[0]), float(xs[1]), float(ys[1])))
        if o.x is None:
            out.append(dataclasses.replace(o, figure=fig))
            continue
        nx, ny = m.apply(o.x, o.y)
        nx, ny = float(nx[0]), float(ny[0])
        if not (0 <= nx <= width - 1 and 0 <= ny <= height - 1):
            continue
        out.append(dataclasses.replace(o, x=nx, y=ny, figure=fig))
    return out


def frame_time(exif) -> datetime | None:
    return exif.datetime_original
