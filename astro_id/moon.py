"""Moon features: name the maria, craters, mountain ranges and Apollo sites on a photo of the Moon.

Plan, method and limits: docs/PLAN-moon.md. Steps: fit the disc (``find_disc``), classify phase / eclipse
(``disc_state``), find the orientation (``--north-angle``, time + site with a level camera, or an albedo fit),
compute the libration and the axis angle (Meeus ch. 53, ``physical_ephemeris``), project the catalog
(``project``), keep what is big enough and visible (``select_features``), and build the annotations payload that
``astro_id.export.render`` draws.

Conventions: image pixels x right, y down. ``phi`` = direction of the Moon's north pole on the image, degrees
counter-clockwise from image up. ``mirrored`` = the picture is mirror-reversed (a star diagonal). Selenographic
longitude is positive east (towards Mare Crisium).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .moon_catalog import FEATURES

log = logging.getLogger("astro_id")

MOON_RADIUS_KM = 1737.4
MIN_FEATURE_PX = 6.0           # a feature is nameable from about this size on the image
APOLLO_SIZE_KM = 40.0          # nominal "where to look" size of a landing site
FIT_MIN_DIAMETER_PX = 40.0     # the albedo fit is not attempted on a smaller disc
FIT_MARGIN_ZERO = 0.25         # albedo fit: relative peak margin at which the certainty is 0
FIT_BLUR = 0.35                # albedo fit: the illumination is flattened with a blur of this x radius
ORIENT_LEVEL_CAMERA = 0.75     # certainty of time + site with the camera roll assumed level
ORIENT_EXIF_TIME = 0.5         # the same with a raw EXIF time (no clock_offset or --time to confirm it)
UMBRA_ILLUM = 0.6              # a feature inside the Earth's shadow still shows, dimmer
MIN_DRAWN_CONFIDENCE = 20      # weaker names stay in the JSON but are not drawn or put in the payload
GROUP = {"id": "moon", "label": "Moon features", "default": True}
KIND_TAG = {"mare": "mare", "mountains": "mountains", "crater": "crater", "landing site": "landing site"}


# ---------------------------------------------------------------- image
def load_rgb(path: Path, max_side: int = 3000) -> tuple[np.ndarray, float]:
    """RGB float array (downscaled so the long side is <= max_side) and the scale factor to full pixels."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        s = 1.0
        if max(w, h) > max_side:
            s = max(w, h) / max_side
            im = im.resize((round(w / s), round(h / s)), Image.Resampling.BOX)
        return np.asarray(im, dtype=np.float64), s


# ---------------------------------------------------------------- disc
@dataclass
class Disc:
    cx: float
    cy: float
    r: float
    rms: float
    coverage: float           # share of the circle (0..1) with limb points on it
    quality: float            # 0..1
    threshold: float

    @property
    def diameter(self) -> float:
        return 2.0 * self.r


def _components(mask: np.ndarray):
    from scipy import ndimage

    lab, n = ndimage.label(mask)
    return lab, n


def _circle3(p):
    (x1, y1), (x2, y2), (x3, y3) = p
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return None
    s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
    ux = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
    uy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
    return ux, uy, math.hypot(x1 - ux, y1 - uy)


def _kasa(x, y):
    a = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    c, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy = c[0] / 2, c[1] / 2
    return cx, cy, math.sqrt(max(c[2] + cx * cx + cy * cy, 0.0))


def fit_circle(bx: np.ndarray, by: np.ndarray, max_r: float, rng=None, iters: int = 600):
    """RANSAC circle on boundary points; a circle that leaves boundary points outside it is penalised (the limb
    encloses the whole lit shape, a terminator arc does not). Returns (cx, cy, r, rms, coverage) or None."""
    n = len(bx)
    if n < 8:
        return None
    rng = rng or np.random.default_rng(1)
    best, best_score = None, -1e18
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        c = _circle3([(bx[i], by[i]) for i in idx])
        if c is None or not (3.0 <= c[2] <= max_r):
            continue
        d = np.hypot(bx - c[0], by - c[1]) - c[2]
        tol = max(1.5, 0.012 * c[2])
        inl = np.abs(d) < tol
        outside = d > 2.5 * tol
        score = inl.sum() - 3.0 * outside.sum()
        if score > best_score:
            best_score, best = score, c
    if best is None:
        return None
    cx, cy, r = best
    for _ in range(3):
        d = np.hypot(bx - cx, by - cy) - r
        inl = np.abs(d) < max(1.5, 0.012 * r)
        if inl.sum() < 6:
            break
        cx, cy, r = _kasa(bx[inl], by[inl])
    d = np.hypot(bx - cx, by - cy) - r
    inl = np.abs(d) < max(1.5, 0.012 * r)
    if inl.sum() < 6:
        return None
    rms = float(np.sqrt(np.mean(d[inl] ** 2)))
    ang = np.degrees(np.arctan2(by[inl] - cy, bx[inl] - cx)) % 360
    coverage = len(np.unique((ang // 5).astype(int))) / 72.0
    return float(cx), float(cy), float(r), rms, float(coverage)


def find_disc(rgb: np.ndarray) -> Disc | None:
    """The Moon's disc: several thresholds from the dim (eclipsed) level to the lit level; the largest
    well-fitting circle wins."""
    from scipy import ndimage

    gray = rgb.sum(axis=2) / 3.0
    small = gray[::4, ::4] if gray.size > 4_000_000 else gray
    # sky level from the darker pixels: a Moon that fills most of the frame must not set the background
    bg = float(np.percentile(small, 15))
    dark = small[small <= np.percentile(small, 40)]
    noise = float(1.4826 * np.median(np.abs(dark - np.median(dark)))) or 1.0
    sm = ndimage.uniform_filter(gray, 3)
    peak = float(np.percentile(sm, 99.995)) if gray.size > 2e5 else float(sm.max())
    if peak - bg < 12 * noise or peak - bg < 10:
        return None
    max_r = 0.75 * min(gray.shape)
    cands: list[Disc] = []
    py, px = np.unravel_index(int(np.argmax(sm)), sm.shape)
    for f in (0.5, 0.3, 0.18, 0.1, 0.06, 0.035):
        t = bg + f * (peak - bg)
        if t < bg + 6 * noise:
            continue
        mask = sm > t
        lab, n = _components(mask)
        if n == 0:
            continue
        # the component holding the brightest pixel; with it, any component that touches its bounding circle
        k = lab[py, px]
        if k == 0:
            sizes = ndimage.sum(mask, lab, range(1, n + 1))
            k = int(np.argmax(sizes)) + 1
        blob = lab == k
        blob = ndimage.binary_fill_holes(blob)
        if blob.sum() < 20:
            continue
        edge = blob & ~ndimage.binary_erosion(blob)
        # ignore edge pixels on the frame border (a cut disc)
        edge[0, :] = edge[-1, :] = False
        edge[:, 0] = edge[:, -1] = False
        ys, xs = np.nonzero(edge)
        fit = fit_circle(xs.astype(float), ys.astype(float), max_r)
        if fit is None:
            continue
        cx, cy, r, rms, cov = fit
        q = min(1.0, cov / 0.5) ** 0.5 * math.exp(-0.5 * (rms / max(0.8, 0.012 * r)) ** 2)
        q = min(1.0, q * 1.15)
        cands.append(Disc(cx, cy, r, rms, cov, round(q, 3), t))
    if not cands:
        return None
    good = [c for c in cands if c.quality >= 0.5]
    pool = good or cands
    # the largest good circle: the dim eclipsed part counts as disc; a halo would fit badly
    best = max(pool, key=lambda c: (round(c.r / max(1.0, 0.03 * c.r)), c.quality))
    for _ in range(3):
        refined = _half_level_fit(sm, best, bg, noise)
        if refined is None:
            break
        best = refined
    return best


def _half_level_fit(sm: np.ndarray, d: Disc, bg: float, noise: float) -> Disc | None:
    """Refit the circle to the points where each ray's brightness falls half way from the disc just inside the
    limb to the sky (a fixed threshold sits out in the blur, or inside a dim eclipsed edge). Rays where the disc
    is not clearly above the sky are skipped."""
    from scipy import ndimage

    ang = np.radians(np.arange(0, 360, 3.0))
    rad = np.linspace(0.6 * d.r, 1.4 * d.r, 161)
    xs = d.cx + np.outer(np.cos(ang), rad)
    ys = d.cy + np.outer(np.sin(ang), rad)
    prof = ndimage.map_coordinates(sm, [ys, xs], order=1, mode="nearest")
    px, py = [], []
    for a, p in zip(ang, prof):
        inner = float(np.median(p[(rad > 0.8 * d.r) & (rad < 0.92 * d.r)]))
        if inner - bg < 10 * noise:
            continue
        half = bg + 0.5 * (inner - bg)
        k = np.nonzero((p[:-1] >= half) & (p[1:] < half) & (rad[:-1] > 0.85 * d.r))[0]
        if len(k):
            i = k[0]
            f = (p[i] - half) / max(p[i] - p[i + 1], 1e-9)
            rr = rad[i] + f * (rad[1] - rad[0])
            px.append(d.cx + rr * math.cos(a))
            py.append(d.cy + rr * math.sin(a))
    if len(px) < 10:
        return None
    x, y = np.array(px), np.array(py)
    keep = np.ones(len(x), bool)
    for _ in range(3):
        cx, cy, r = _kasa(x[keep], y[keep])
        res = np.hypot(x - cx, y - cy) - r
        sd = max(0.3, 1.4826 * float(np.median(np.abs(res[keep]))))
        keep = np.abs(res) < 3 * sd
        if keep.sum() < 8:
            return None
    if abs(r - d.r) > 0.25 * d.r or math.hypot(cx - d.cx, cy - d.cy) > 0.25 * d.r:
        return None
    rms = float(np.sqrt(np.mean(res[keep] ** 2)))
    cov = keep.sum() / float(len(ang))
    q = min(1.0, (cov / 0.5) ** 0.5 * math.exp(-0.5 * (rms / max(0.8, 0.012 * r)) ** 2) * 1.15)
    return Disc(float(cx), float(cy), float(r), rms, round(float(cov), 3), round(max(q, d.quality * 0.9), 3),
                d.threshold)


# ---------------------------------------------------------------- state
@dataclass
class State:
    state: str                 # full | gibbous | half | crescent | eclipse-partial | eclipse-total
    lit_fraction: float
    dark_level: float          # unlit-part brightness / lit brightness
    red_ratio: float           # R/G of the unlit part (eclipse: > 1.15)
    limb_span: float = 360.0   # degrees of the limb that are sunlit (a phase: 180, full: 360)
    lit_mask: np.ndarray = field(repr=False, default=None)       # directly sunlit
    visible_mask: np.ndarray = field(repr=False, default=None)   # surface visible (sunlit, or in a bright umbra)


LIT_FRACTION_LEVEL = 0.22
TOTALITY_RED = 1.5             # R/G of the whole disc above which it is the Earth's shadow, not sunlight


def disc_masks(shape, d: Disc, scale: float = 1.0):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return np.hypot(xx - d.cx, yy - d.cy) <= d.r * scale


def lit_limb_span(lit: np.ndarray, d: Disc) -> float:
    """Degrees of the limb (a ring at 0.8-0.97 R) that are lit, in 5-degree bins."""
    yy, xx = np.mgrid[0:lit.shape[0], 0:lit.shape[1]]
    rr = np.hypot(xx - d.cx, yy - d.cy) / d.r
    # the outer ring: there a phase terminator adds at most ~12 deg per cusp to the 180
    ring = (rr > min(0.93, 1 - 1.5 / d.r)) & (rr < 0.995)
    if ring.sum() < 20:
        return 360.0
    ang = (np.degrees(np.arctan2(yy[ring] - d.cy, xx[ring] - d.cx)) % 360 // 5).astype(int)
    tot = np.bincount(ang, minlength=72)
    on = np.bincount(ang, weights=lit[ring].astype(float), minlength=72)
    return float(5 * np.sum((tot > 0) & (on > 0.5 * np.maximum(tot, 1))))


def disc_state(rgb: np.ndarray, d: Disc) -> State:
    from scipy import ndimage

    gray = ndimage.uniform_filter(rgb.sum(axis=2) / 3.0, 3)
    disc = disc_masks(gray.shape, d)
    inner = disc_masks(gray.shape, d, 0.92)
    bg = float(np.median(gray[~disc_masks(gray.shape, d, 1.3)])) if (~disc).any() else 0.0
    v = gray[disc] - bg
    lit_level = float(np.percentile(v, 97))
    # sunlit = above 22 % of the bright level: the maria are ~30-50 % of the highlands, the night side of a
    # phase is black, the Earth's umbra is dim (and red)
    lit = disc & ((gray - bg) > LIT_FRACTION_LEVEL * lit_level)
    bright = disc & ((gray - bg) > 0.5 * lit_level)
    lit_frac = float(lit.sum() / max(1, disc.sum()))
    unlit = inner & ~bright
    if unlit.sum() >= 5:
        dark = float(np.median(gray[unlit] - bg)) / max(lit_level, 1e-6)
        r = rgb[..., 0][unlit].mean() - bg
        g = rgb[..., 1][unlit].mean() - bg
        red = float(r / max(g, 1e-6)) if g > 0 else 9.9
    else:
        dark, red = 0.0, 1.0
    rg_all = (rgb[..., 0][disc].mean() - bg) / max(rgb[..., 1][disc].mean() - bg, 1e-6)
    span = lit_limb_span(lit, d)
    # a phase always lights exactly half of the limb (all of it at full Moon); the Earth's shadow does not
    shadow_edge = lit_frac < 0.95 and (span < 140 or 230 < span < 320)
    visible = lit
    umbra_shows = dark > 0.03 and red > 1.15 and unlit.sum() > 0.08 * disc.sum()
    if umbra_shows or shadow_edge:
        lit = bright
        lit_frac = float(bright.sum() / max(1, disc.sum()))
        st = "eclipse-total" if lit_frac < 0.03 else "eclipse-partial"
        visible = disc.copy() if umbra_shows else bright
    elif rg_all > TOTALITY_RED:
        # the whole disc evenly red: totality (a sunlit Moon, even low in the sky, is not this red)
        st, visible, lit, lit_frac = "eclipse-total", disc.copy(), np.zeros_like(disc), 0.0
    elif lit_frac > 0.95:
        st = "full"
    elif lit_frac > 0.55:
        st = "gibbous"
    elif lit_frac > 0.4:
        st = "half"
    else:
        st = "crescent"
    return State(st, round(lit_frac, 3), round(dark, 3), round(red, 2), span, lit, visible)


# ---------------------------------------------------------------- ephemeris (Meeus, Astronomical Algorithms ch. 53)
I_DEG = 1.54242                 # inclination of the mean lunar equator to the ecliptic


def mean_elements(T: float) -> tuple[float, float]:
    """Moon's argument of latitude F and longitude of the ascending node Omega (deg), Meeus 47.5 / 47.7."""
    F = 93.2720950 + 483202.0175233 * T - 0.0036539 * T * T - T ** 3 / 3526000 + T ** 4 / 863310000
    om = 125.0445479 - 1934.1362891 * T + 0.0020754 * T * T + T ** 3 / 467441 - T ** 4 / 60616000
    return F % 360.0, om % 360.0


def optical_libration(lam: float, beta: float, dpsi: float, T: float) -> tuple[float, float]:
    """Optical libration (l, b) in degrees from the apparent geocentric ecliptic longitude/latitude (deg) and
    the nutation in longitude (deg). l = selenographic longitude, b = latitude of the sub-Earth point."""
    F, om = mean_elements(T)
    I = math.radians(I_DEG)
    W = math.radians(lam - dpsi - om)
    b_ = math.radians(beta)
    A = math.atan2(math.sin(W) * math.cos(b_) * math.cos(I) - math.sin(b_) * math.sin(I),
                   math.cos(W) * math.cos(b_))
    l = (math.degrees(A) - F + 180.0) % 360.0 - 180.0
    b = math.degrees(math.asin(-math.sin(W) * math.cos(b_) * math.sin(I) - math.sin(b_) * math.cos(I)))
    return l, b


def position_angle_axis(dpsi: float, eps: float, alpha: float, b: float, T: float) -> float:
    """Position angle P (deg, from celestial north through east) of the Moon's rotation axis; physical
    libration (rho, sigma < 0.04 deg) ignored. eps = true obliquity, alpha = apparent RA (deg)."""
    _, om = mean_elements(T)
    I = math.radians(I_DEG)
    V = math.radians(om + dpsi)
    X = math.sin(I) * math.sin(V)
    Y = math.sin(I) * math.cos(V) * math.cos(math.radians(eps)) - math.cos(I) * math.sin(math.radians(eps))
    w = math.atan2(X, Y)
    P = math.asin(max(-1.0, min(1.0, math.hypot(X, Y) * math.cos(math.radians(alpha) - w)
                                / math.cos(math.radians(b)))))
    return math.degrees(P) % 360.0


@dataclass
class Ephem:
    l: float          # sub-Earth selenographic longitude (deg)
    b: float          # sub-Earth selenographic latitude (deg)
    P: float          # position angle of the axis (deg)
    q: float | None   # parallactic angle (deg), when a site is known
    alt: float | None
    ra: float
    dec: float
    illuminated: float = 1.0   # predicted sunlit fraction of the disc (ignores the Earth's shadow)


def physical_ephemeris(t: datetime, site=None) -> Ephem:
    """Libration, axis angle and (with a site) the parallactic angle of the Moon at ``t`` (aware datetime)."""
    from skyfield.api import wgs84
    from skyfield.nutationlib import iau2000a_radians, mean_obliquity

    from .data import ephemeris

    ts, eph = ephemeris()
    tt = ts.from_datetime(t.astimezone(timezone.utc))
    earth, moon = eph["earth"], eph["moon"]
    app = earth.at(tt).observe(moon).apparent()
    lat_e, lon_e, _ = app.ecliptic_latlon(epoch="date")
    ra, dec, _ = app.radec(epoch="date")
    dpsi, deps = iau2000a_radians(tt)
    eps = mean_obliquity(tt.tdb) / 3600.0 + math.degrees(deps)
    T = (tt.tt - 2451545.0) / 36525.0
    l, b = optical_libration(lon_e.degrees, lat_e.degrees, math.degrees(dpsi), T)
    P = position_angle_axis(math.degrees(dpsi), eps, ra._degrees, b, T)
    q = alt = None
    if site is not None:
        obs = earth + wgs84.latlon(site.lat, site.lon, elevation_m=site.elev_m or 0.0)
        topo = obs.at(tt).observe(moon).apparent()
        ha, tdec, _ = topo.hadec()
        alt_, _, _ = topo.altaz()
        q = parallactic_angle(ha.hours * 15.0, tdec.degrees, site.lat)
        alt = alt_.degrees
    illum = float(app.fraction_illuminated(eph["sun"]))
    return Ephem(l, b, P, q, alt, ra._degrees, dec.degrees, illum)


def parallactic_angle(ha_deg: float, dec_deg: float, lat_deg: float) -> float:
    """Angle at the object from the direction of the north celestial pole to the zenith (deg, positive west of
    the meridian; Meeus 14.1)."""
    H, d, f = (math.radians(v) for v in (ha_deg, dec_deg, lat_deg))
    return math.degrees(math.atan2(math.sin(H), math.tan(f) * math.cos(d) - math.sin(d) * math.cos(H)))


# ---------------------------------------------------------------- projection
def axes(phi: float, mirrored: bool):
    """Image unit vectors of lunar north (pole) and selenographic east for a pole at ``phi`` deg CCW from up."""
    p = math.radians(phi)
    pole = np.array([-math.sin(p), -math.cos(p)])
    east = np.array([math.cos(p), -math.sin(p)])          # pole turned 90 deg clockwise on the screen
    return pole, (-east if mirrored else east)


def ortho(lat, lon, l0: float, b0: float):
    """Orthographic (x' east, y' north, z towards the observer) for sub-Earth point (b0, l0); arrays."""
    la, lo = np.radians(lat), np.radians(np.asarray(lon) - l0)
    B = math.radians(b0)
    x = np.cos(la) * np.sin(lo)
    y = math.cos(B) * np.sin(la) - math.sin(B) * np.cos(la) * np.cos(lo)
    z = math.cos(B) * np.cos(la) * np.cos(lo) + math.sin(B) * np.sin(la)
    return x, y, z


def project(lat, lon, disc: Disc, phi: float, mirrored: bool, l0: float = 0.0, b0: float = 0.0):
    """Pixel x, y and the visibility (z > 0) of selenographic points."""
    x, y, z = ortho(lat, lon, l0, b0)
    pole, east = axes(phi, mirrored)
    px = disc.cx + disc.r * (x * east[0] + y * pole[0])
    py = disc.cy + disc.r * (x * east[1] + y * pole[1])
    return px, py, z


def phi_from_north(north_angle: float, P: float, mirrored: bool) -> float:
    """Pole direction on the image from the direction of celestial north (deg CCW from up) and the axis angle."""
    return (north_angle - P if mirrored else north_angle + P) % 360.0


# ---------------------------------------------------------------- albedo fit
def _mare_list():
    out = []
    for nm, kind, la, lo, dkm in FEATURES:
        if kind != "mare" or dkm < 200:
            continue
        rad = dkm / 2.0 / MOON_RADIUS_KM
        if nm == "Oceanus Procellarum":
            rad *= 0.65
        out.append((la, lo, rad))
    return out


def albedo_model(u: np.ndarray, v: np.ndarray, l0: float = 0.0, b0: float = 0.0) -> np.ndarray:
    """Model albedo at disc coordinates (u east, v north, unit radius): 1 highlands, 0 maria (crude: maria as
    spherical caps of their catalog size)."""
    w = np.sqrt(np.clip(1 - u * u - v * v, 0, 1))
    L, B = math.radians(l0), math.radians(b0)
    eo = np.array([math.cos(B) * math.cos(L), math.cos(B) * math.sin(L), math.sin(B)])
    ee = np.array([-math.sin(L), math.cos(L), 0.0])
    en = np.array([-math.sin(B) * math.cos(L), -math.sin(B) * math.sin(L), math.cos(B)])
    p = u[..., None] * ee + v[..., None] * en + w[..., None] * eo
    out = np.ones(u.shape)
    for la, lo, rad in _mare_list():
        c = np.array([math.cos(math.radians(la)) * math.cos(math.radians(lo)),
                      math.cos(math.radians(la)) * math.sin(math.radians(lo)), math.sin(math.radians(la))])
        ang = np.arccos(np.clip(p @ c, -1, 1))
        out = np.minimum(out, np.clip((ang - rad) / (0.35 * rad), 0, 1))
    return out


def fit_orientation(rgb: np.ndarray, d: Disc, st: State, l0: float = 0.0, b0: float = 0.0,
                    mirror_states=(False,)):
    """Correlate the disc's albedo pattern (illumination gradient divided out) with the model over the pole
    angle (and the mirror states asked for; a camera lens does not mirror). Returns (phi, mirrored, certainty,
    corr) or None when the disc is too small."""
    from scipy import ndimage

    if d.diameter < FIT_MIN_DIAMETER_PX:
        return None
    gray = rgb.sum(axis=2) / 3.0
    y0, y1 = max(0, int(d.cy - d.r - 2)), min(gray.shape[0], int(d.cy + d.r + 3))
    x0, x1 = max(0, int(d.cx - d.r - 2)), min(gray.shape[1], int(d.cx + d.r + 3))
    g = gray[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    du, dv = (xx - d.cx) / d.r, (yy - d.cy) / d.r
    inside = du * du + dv * dv <= 0.85 ** 2
    sig = max(1.5, FIT_BLUR * d.r)
    lit = st.lit_mask[y0:y1, x0:x1]
    vis = st.visible_mask[y0:y1, x0:x1]
    # each region (sunlit, and the umbra when it shows) is flattened on its own, and a band along the shadow
    # edge is left out, so the step at the shadow edge does not drive the fit
    hp = np.zeros_like(g)
    use = np.zeros_like(inside)
    band = max(1, int(round(0.08 * d.r)))
    for region in (lit, vis & ~lit):
        reg = region & inside
        if reg.sum() < 20:
            continue
        core = ndimage.binary_erosion(region, iterations=band) & inside if region is not lit or st.state.startswith(
            "eclipse") else reg
        m = reg.astype(float)
        blur = ndimage.gaussian_filter(g * m, sig) / np.maximum(ndimage.gaussian_filter(m, sig), 1e-6)
        hp[core] = g[core] / np.maximum(blur[core], 1e-6)
        use |= core
    if use.sum() < 60:
        return None
    obs, ox, oy = hp[use], du[use], dv[use]
    if obs.size > 2500:                      # a large disc: an even subsample is plenty for a 2-deg search
        k = np.linspace(0, obs.size - 1, 2500).astype(int)
        obs, ox, oy = obs[k], ox[k], oy[k]
    obs = (obs - obs.mean()) / (obs.std() or 1.0)
    best = []
    for mirrored in mirror_states:
        for phi in range(0, 360, 2):
            pole, east = axes(phi, mirrored)
            u = ox * east[0] + oy * east[1]
            v = ox * pole[0] + oy * pole[1]
            mod = albedo_model(u, v, l0, b0)
            if mod.std() < 1e-6:
                continue
            mod = (mod - mod.mean()) / mod.std()
            best.append((float(np.mean(obs * mod)), phi, mirrored))
    if not best:
        return None
    best.sort(reverse=True)
    c, phi, mir = best[0]
    second = max((b[0] for b in best[1:] if b[2] != mir or abs((b[1] - phi + 180) % 360 - 180) > 30), default=0.0)
    # certainty from the relative margin of the peak over the best angle more than 30 deg away: on NASA SVS
    # frames turned and shrunk to 65-650 px (docs/PLAN-moon.md), every right fit had a margin >= 0.27 of the
    # peak and every wrong one <= 0.28; 0.25 -> 0, 0.5 and up -> 0.9 (never 1: the model is crude)
    rel = (c - second) / c if c > 0 else 0.0
    certainty = 0.9 * max(0.0, min(1.0, (rel - FIT_MARGIN_ZERO) / 0.25))
    return float(phi), bool(mir), round(certainty, 3), round(c, 3), round(rel, 3)


# ---------------------------------------------------------------- features
@dataclass
class Feature:
    name: str
    kind: str
    lat: float
    lon: float
    x: float
    y: float
    size_px: float
    confidence: int
    tag: str


def select_features(d: Disc, st: State | None, phi: float, mirrored: bool, q_orient: float,
                    l0: float = 0.0, b0: float = 0.0, min_px: float = MIN_FEATURE_PX) -> list[Feature]:
    names = [f[0] for f in FEATURES]
    kind = [f[1] for f in FEATURES]
    lat = np.array([f[2] for f in FEATURES])
    lon = np.array([f[3] for f in FEATURES])
    dkm = np.array([f[4] if f[4] > 0 else APOLLO_SIZE_KM for f in FEATURES], dtype=float)
    px, py, z = project(lat, lon, d, phi, mirrored, l0, b0)
    out: list[Feature] = []
    for i in range(len(FEATURES)):
        if z[i] <= 0.05:
            continue
        size = dkm[i] / MOON_RADIUS_KM * d.r * (math.sqrt(z[i]) if kind[i] != "mountains" else 1.0)
        if kind[i] == "landing site":
            size = min(size, dkm[i] / MOON_RADIUS_KM * d.r)
        if size < min_px:
            continue
        q_size = 1.0 - math.exp(-((size / min_px) ** 2))
        q_illum = 1.0
        if st is not None and st.lit_mask is not None:
            rr = max(1.0, 0.35 * size)
            ys = np.clip(np.round(py[i] + rr * np.array([0, 1, -1, 0, 0, .7, -.7, .7, -.7])).astype(int), 0,
                         st.lit_mask.shape[0] - 1)
            xs = np.clip(np.round(px[i] + rr * np.array([0, 0, 0, 1, -1, .7, .7, -.7, -.7])).astype(int), 0,
                         st.lit_mask.shape[1] - 1)
            seen = float(st.visible_mask[ys, xs].mean())
            if seen < 0.3:                      # on the night side, or in a black umbra
                continue
            sun = float(st.lit_mask[ys, xs].mean())
            q_illum = sun + (seen - sun) * UMBRA_ILLUM
        conf = int(round(100 * d.quality * q_orient * q_size * q_illum))
        out.append(Feature(names[i], kind[i], float(lat[i]), float(lon[i]), round(float(px[i]), 1),
                           round(float(py[i]), 1), round(float(size), 1), max(0, min(100, conf)),
                           names[i].lower()))
    out.sort(key=lambda f: -f.confidence)
    return out


def size_message(d: Disc) -> str:
    """What the disc size allows, in words."""
    need_mare = 2 * MIN_FEATURE_PX * MOON_RADIUS_KM / 556.0       # Mare Crisium, the smallest distinct mare
    need_crater = 2 * MIN_FEATURE_PX * MOON_RADIUS_KM / 85.0      # Tycho
    if d.diameter < need_mare:
        return (f"the Moon is {d.diameter:.0f} px across here; maria need about {need_mare:.0f} px, "
                f"the large craters about {need_crater:.0f} px")
    if d.diameter < need_crater:
        return (f"the Moon is {d.diameter:.0f} px across here: only the largest maria can be named; craters "
                f"like Tycho need about {need_crater:.0f} px")
    return f"the Moon is {d.diameter:.0f} px across"


# ---------------------------------------------------------------- one photo
@dataclass
class MoonResult:
    file: str
    found: bool
    message: str
    width: int = 0
    height: int = 0
    disc: dict | None = None
    state: dict | None = None
    orientation: dict | None = None
    ephemeris: dict | None = None
    features: list[dict] = field(default_factory=list)
    annotations: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def analyse(path: Path, t: datetime | None = None, site=None, north_angle: float | None = None,
            mirrored: bool = False, fit: bool = True, time_confirmed: bool = True) -> MoonResult:
    from PIL import Image

    with Image.open(path) as im:
        W, H = im.size
    rgb, s = load_rgb(path)
    d = find_disc(rgb)
    if d is None:
        return MoonResult(str(path), False, "no Moon disc found", W, H)
    st = disc_state(rgb, d)
    eph = None
    if t is not None:
        try:
            eph = physical_ephemeris(t, site)
        except Exception as exc:          # no ephemeris file and no network
            log.warning("Moon ephemeris unavailable: %s", exc)
    l0, b0 = (eph.l, eph.b) if eph else (0.0, 0.0)
    if eph is not None and eph.illuminated > 0.97 and st.state in ("gibbous", "half", "crescent"):
        # the Sun lights the whole disc at this time: the missing part is the Earth's shadow
        st.state = "eclipse-partial"
    orient: dict = {"method": "none", "certainty": 0.0}
    phi = None
    if north_angle is not None:
        P = eph.P if eph else 0.0
        phi = phi_from_north(north_angle, P, mirrored)
        orient = {"method": "given", "certainty": 1.0 if eph else 0.6, "north_angle_deg": north_angle}
    elif eph is not None and eph.q is not None:
        north = -eph.q
        phi = phi_from_north(north, eph.P, mirrored)
        orient = {"method": "time+site, camera assumed level",
                  "certainty": ORIENT_LEVEL_CAMERA if time_confirmed else ORIENT_EXIF_TIME,
                  "north_angle_deg": round(north % 360, 2)}
    elif fit:
        f = fit_orientation(rgb, d, st, l0, b0, (mirrored,))
        if f is not None and f[2] > 0:
            phi, mirrored = f[0], f[1]
            orient = {"method": "albedo fit", "certainty": f[2], "correlation": f[3], "margin": f[4]}
        elif f is not None:
            orient = {"method": "albedo fit failed", "certainty": 0.0, "correlation": f[3], "margin": f[4],
                      "best_pole_angle_deg": f[0]}
    if phi is not None:
        orient.update({"pole_angle_deg": round(phi % 360, 2), "mirrored": mirrored})
    feats = select_features(d, st, phi, mirrored, orient["certainty"], l0, b0) if phi is not None else []
    msg = size_message(Disc(d.cx * s, d.cy * s, d.r * s, d.rms, d.coverage, d.quality, d.threshold))
    if eph is None and st.state in ("crescent", "half", "gibbous"):
        msg += f"; {st.state} (or the edge of the Earth's shadow in an eclipse: --time tells them apart)"
    if phi is None:
        msg += "; orientation unknown (give --time and --site, or --north-angle)"
    full = Disc(d.cx * s, d.cy * s, d.r * s, d.rms * s, d.coverage, d.quality, d.threshold)
    for f in feats:
        f.x, f.y, f.size_px = round(f.x * s, 1), round(f.y * s, 1), round(f.size_px * s, 1)
    res = MoonResult(str(path), True, msg, W, H,
                     {"cx": round(full.cx, 1), "cy": round(full.cy, 1), "r": round(full.r, 1),
                      "diameter_px": round(full.diameter, 1), "rms_px": round(full.rms, 2),
                      "coverage": round(d.coverage, 2), "quality": d.quality},
                     {"state": st.state, "lit_fraction": st.lit_fraction, "dark_level": st.dark_level,
                      "red_ratio": st.red_ratio, "limb_lit_deg": st.limb_span}, orient,
                     ({"sub_earth_lon": round(eph.l, 3), "sub_earth_lat": round(eph.b, 3),
                       "axis_pa": round(eph.P, 3), "parallactic": None if eph.q is None else round(eph.q, 2),
                       "alt": None if eph.alt is None else round(eph.alt, 2),
                       "illuminated": round(eph.illuminated, 3)} if eph else None),
                     [asdict(f) for f in feats])
    res.annotations = build_payload(res, full, st.state, orient, feats)
    return res


def legend(res: MoonResult, state: str, orient: dict) -> str:
    parts = [f"Moon {res.disc['diameter_px']:.0f} px", state.replace("-", " ")]
    m = orient.get("method", "none")
    parts.append("orientation unknown" if m in ("none", "albedo fit failed")
                 else f"orientation: {m} ({int(round(100 * orient['certainty']))}%)")
    return " · ".join(parts)


def build_payload(res: MoonResult, d: Disc, state: str, orient: dict, feats: list[Feature]) -> dict:
    from .annotations import SCORE, VERSION, cap_size, slug

    items = []
    used: dict[str, int] = {}
    for f in feats:
        if f.confidence < MIN_DRAWN_CONFIDENCE:
            continue
        base = slug(f.name)
        used[base] = used.get(base, 0) + 1
        it = {"id": base if used[base] == 1 else f"{base}-{used[base]}", "label": f.name, "group": "moon",
              "score": f.confidence, "x": f.x, "y": f.y}
        if f.kind == "crater":                 # the rim, to scale
            it["shape"], it["r"] = "circle", round(max(f.size_px / 2, 2.0), 1)
        else:                                  # maria, ranges, sites: a mark at the centre (a mare's outline
            it["shape"], it["r"] = "point", 2.0   # is not a circle)
        items.append(it)
    return cap_size({"version": VERSION, "source": "astro-id", "title": "Moon features",
                     "image": {"width": int(res.width), "height": int(res.height)},
                     "legend": legend(res, state, orient), "groups": [dict(GROUP)], "metrics": [],
                     "score": dict(SCORE), "items": items})


def render(src: Path, out: Path, res: MoonResult, scores: bool = False) -> str:
    """Labeled copy: the limb circle, the names, the legend. Uses the sky-label renderer; the limb item has no
    label. Small discs are drawn on a crop around the Moon so the result can be read."""
    import copy
    import tempfile

    from PIL import Image

    from .export import render as render_payload

    p = copy.deepcopy(res.annotations)
    d = res.disc
    half = max(3.0 * d["r"], 150.0)
    if 2 * half < 0.6 * min(res.width, res.height):
        # a small Moon in a big frame: draw on a crop around it, scaled up, so the names can be read
        x0, y0 = max(0, int(d["cx"] - half)), max(0, int(d["cy"] - half))
        x1, y1 = min(res.width, int(d["cx"] + half)), min(res.height, int(d["cy"] + half))
        with Image.open(src) as im:
            crop = im.convert("RGB").crop((x0, y0, x1, y1))
        k = 1200.0 / crop.size[0]
        crop = crop.resize((1200, round(crop.size[1] * k)), Image.Resampling.LANCZOS)
        for it in p["items"]:
            it["x"], it["y"] = round((it["x"] - x0) * k, 1), round((it["y"] - y0) * k, 1)
            it["r"] = round(it["r"] * k, 1)
        p["image"] = {"width": crop.size[0], "height": crop.size[1]}
        p["legend"] += f" · crop x{k:.1f}"
        limb = ((d["cx"] - x0) * k, (d["cy"] - y0) * k, d["r"] * k)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "crop.png"
            crop.save(tmp)
            r = render_payload(tmp, out, p, {"score": 0}, lines=False, scores=scores, width=1200)
    else:
        width = min(3000, max(1200, res.width))
        limb = (d["cx"], d["cy"], d["r"])
        r = render_payload(src, out, p, {"score": 0}, lines=False, scores=scores, width=width)
    _draw_limb(Path(out), limb, p["image"]["width"])
    return r["out"]


def _draw_limb(out: Path, limb, payload_width: int) -> None:
    """The fitted limb as a thin dashed circle, so it is clear what was measured."""
    from PIL import Image, ImageDraw

    with Image.open(out) as im:
        im = im.convert("RGB")
    s = im.size[0] / float(payload_width)
    cx, cy, r = (v * s for v in limb)
    d = ImageDraw.Draw(im)
    for a in range(0, 360, 6):
        d.arc([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], a, a + 3, fill=(120, 200, 255), width=2)
    im.save(out, "JPEG", quality=90)


# ---------------------------------------------------------------- CLI
def add_parser(sub) -> None:
    import argparse

    from .vocab import exit_epilog

    m = sub.add_parser("moon", help="name the maria, craters and mountain ranges on a photo of the Moon",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="Find the Moon's disc in a photo, work out how it is turned, and name its maria, "
                                   "large craters, mountain ranges and Apollo landing sites with a confidence. The "
                                   "labeled copy is <name>_moon.jpg. Orientation: --north-angle, else the time and "
                                   "site (camera assumed level), else a fit of the dark maria. A small Moon (under "
                                   "~60 px) gets no names, and says what size it would need. See docs/PLAN-moon.md.",
                       epilog=exit_epilog("moon"))
    m.add_argument("target", help="photo or folder")
    m.add_argument("--time", help="true time of the photo, ISO-8601 (default: EXIF time + astro-id.json clock_offset)")
    m.add_argument("--site", help="lat,lon[,elev_m] (default: astro-id.json site)")
    m.add_argument("--timezone", help="IANA zone of a naive --time or of the EXIF time")
    m.add_argument("--north-angle", type=float,
                   help="direction of celestial north on the photo, degrees counter-clockwise from up")
    m.add_argument("--mirror", action="store_true", help="the picture is mirror-reversed (star diagonal)")
    m.add_argument("--no-fit", action="store_true", help="never guess the orientation from the maria")
    m.add_argument("--to", help="folder for the labeled copies (default: next to each photo)")
    m.add_argument("--no-overlay", action="store_true", help="no labeled copy")
    m.add_argument("--overlay-scores", action="store_true", help="print the confidence after each name")
    m.add_argument("--tag-style", choices=["plain", "kind"], default="plain",
                   help="plain: 'tycho'; kind: 'moon/tycho'")
    m.add_argument("--json", action="store_true", help="print the results as JSON on stdout")
    m.add_argument("-v", "--verbose", action="store_true")


def _frame_time(path: Path, args):
    """(true time or None, site or None): --time, else EXIF time + astro-id.json clock_offset/timezone."""
    from .config import resolve
    from .imageio import read_exif
    from .timeutil import true_time

    cfg = resolve(path.parent, None, None, args.site, args.time, args.timezone)
    site = cfg.site
    if cfg.time_override is not None:
        return cfg.time_override, site, True
    ex = read_exif(path)
    if ex.datetime_original is None:
        return None, site, False
    # an EXIF time with no clock_offset stated anywhere is unconfirmed (as for the Sun, Moon and planets in solve)
    return true_time(ex.datetime_original, cfg.offset, cfg.timezone), site, cfg.time_source != "exif"


def run(args) -> int:
    from .imageio import collect

    target = Path(args.target)
    if not target.exists():
        log.error("not found: %s", target)
        return 2
    files = [p for p in collect(target) if not p.stem.endswith(("_moon", "_astro"))]
    if not files:
        log.error("no photos in %s", target)
        return 2
    out_dir = Path(args.to) if args.to else None
    results = []
    for p in files:
        try:
            t, site, confirmed = _frame_time(p, args)
        except ValueError as exc:
            log.error("%s", exc)
            return 2
        res = analyse(p, t, site, args.north_angle, args.mirror, fit=not args.no_fit, time_confirmed=confirmed)
        for f in res.features:
            f["tag"] = ("moon/" + f["tag"]) if args.tag_style == "kind" else f["tag"]
        if res.found and not args.no_overlay:
            out = (out_dir or p.parent) / f"{p.stem}_moon.jpg"
            res_out = render(p, out, res, scores=args.overlay_scores)
            log.info("%s: %s -> %s", p.name, res.message, res_out)
        else:
            log.info("%s: %s", p.name, res.message)
        names = ", ".join(f"{f['name']} {f['confidence']}" for f in res.features[:12])
        if names:
            log.info("  %s", names)
        results.append(res.as_dict())
    if args.json:
        print(json.dumps({"contract": 1, "moon": results}, indent=1))
    return 0 if any(r["found"] for r in results) else 1
