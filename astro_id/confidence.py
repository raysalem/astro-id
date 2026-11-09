"""Confidence math. Everything here is pure and unit-tested.

confidence = 100 x solve_quality x position_certainty x detectability x margin  (x bonus, capped at 100)
"""
from __future__ import annotations

import math

HIGH = 80
LIKELY = 65
EXIF_TIME_CAP = 64  # time-dependent objects without a configured clock are at most "candidate"
HORIZON_MARGIN_DEG = 3.0

GRADE_QUALITY = {"A": 1.0, "B": 0.92, "C": 0.75}


def tier(conf: int) -> str:
    if conf >= HIGH:
        return "high"
    if conf >= LIKELY:
        return "likely"
    return "candidate"


def solve_quality(grade: str | None) -> float:
    return GRADE_QUALITY.get(grade or "", 0.0)


def position_certainty(err_px: float, size_px: float) -> float:
    """How sure we are the object sits where we say, given the local fit error and the object's size.

    Gaussian in err/size: an error of one object-size (radius) costs ~40%; floor 0.05."""
    size = max(size_px, 1.0)
    return max(0.05, math.exp(-0.5 * (max(err_px, 0.0) / size) ** 2))


def detectability(mag_eff: float, lim_mag: float | None, softness: float = 0.6) -> float:
    """Logistic in (limiting magnitude - effective magnitude): 0.5 at the limit, ~0.84 one softness brighter."""
    if lim_mag is None:
        lim_mag = 6.0
    z = (lim_mag - mag_eff) / softness
    if z > 30:
        return 1.0
    if z < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


EXTENDED_PENALTY = 1.5  # an extended object must be this much brighter than the point-source limit


def extended_mag(mag: float, size_arcmin: float, psf_arcmin: float) -> float:
    """Effective point-source magnitude for an extended object: light spread over a larger area is harder
    to see. Grows 2.5 mag per decade of size beyond the PSF (between total and surface brightness), plus a
    fixed EXTENDED_PENALTY because a diffuse patch needs more contrast than a star."""
    ratio = max(1.0, size_arcmin / max(psf_arcmin, 1e-3))
    return mag + 2.5 * math.log10(ratio) + EXTENDED_PENALTY


def frame_margin(fraction_inside: float) -> float:
    """1 when the object is wholly in frame; falls linearly to 0.4 when only its center is."""
    f = min(max(fraction_inside, 0.0), 1.0)
    return 0.4 + 0.6 * f


def horizon_margin(min_alt_deg: float | None) -> float:
    """Worst-case altitude over the time window. >= 3 deg: 1. 0..3 deg: 0.6..1. Below horizon: 0.4."""
    if min_alt_deg is None:
        return 1.0
    if min_alt_deg >= HORIZON_MARGIN_DEG:
        return 1.0
    if min_alt_deg < 0:
        return 0.4
    return 0.6 + 0.4 * (min_alt_deg / HORIZON_MARGIN_DEG)


def combine(quality: float, position: float, detect: float, margin: float, bonus: float = 1.0,
            cap: int | None = None) -> int:
    c = 100.0 * quality * position * detect * margin * bonus
    c = int(round(min(100.0, max(0.0, c))))
    if cap is not None:
        c = min(c, cap)
    return c


def constellation_confidence(quality: float, matched: int, total: int) -> int:
    if total <= 0:
        return 0
    return combine(quality, 1.0, matched / total, 1.0)
