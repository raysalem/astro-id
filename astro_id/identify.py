"""Identify catalog objects in a solved frame and score them."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from . import confidence as C
from . import data
from . import tagpolicy as TP
from .config import Site
from .solve import Solution, safe_world2pix
from .timeutil import window

PLANETS = [  # (display name, ephemeris key, kind, fallback magnitude)
    ("Mercury", "mercury", "planet", 0.0), ("Venus", "venus", "planet", -4.2), ("Mars", "mars", "planet", 1.0),
    ("Jupiter", "jupiter barycenter", "planet", -2.3), ("Saturn", "saturn barycenter", "planet", 0.7),
    ("Uranus", "uranus barycenter", "planet", 5.7), ("Neptune", "neptune barycenter", "planet", 7.8),
    ("Moon", "moon", "moon", -10.0), ("Sun", "sun", "star", -26.7),
]
BRIGHT_BODY_MAG = 2.0  # bodies at least this bright must coincide with one of the brightest sources
STAR_MAG_MAX = 4.5  # named stars fainter than this are not reported (clutter)


@dataclass
class Obj:
    name: str
    kind: str
    confidence: int
    x: float | None
    y: float | None
    radius_px: float = 0.0
    alt_min: float | None = None
    sep_deg: float | None = None  # planets/Moon: distance from prediction to nearest bright source
    mag: float | None = None
    figure: list[tuple[float, float, float, float]] = field(default_factory=list)  # constellation segments
    figure_sky: list[tuple[float, float, float, float]] = field(default_factory=list)  # same segments, (ra,dec,ra,dec) deg
    level: int = TP.LEVEL_STAR
    ra: float | None = None
    dec: float | None = None
    parent: str | None = None  # tag of the constellation it lies in (None for constellations)
    member_of: str | None = None  # tag of the cluster/asterism that subsumes it
    groups: list[str] = field(default_factory=list)  # every group it is geometrically part of
    taggable: bool = True
    style: str = "plain"
    hips: tuple[int, ...] = ()  # star: its HIP id; asterism: member HIP ids

    @property
    def tier(self) -> str:
        return C.tier(self.confidence)

    @property
    def tag(self) -> str:
        return TP.styled_tag(self.name, self.level, self.kind, self.style)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "tag": self.tag, "kind": self.kind, "confidence": int(self.confidence),
            "tier": self.tier,
            "x": None if self.x is None else round(float(self.x), 1),
            "y": None if self.y is None else round(float(self.y), 1),
            "level": int(self.level),
            "magnitude": None if self.mag is None or self.level == TP.LEVEL_CONSTELLATION
            else round(float(self.mag), 2),
            "parent": self.parent, "member_of": self.member_of, "taggable": bool(self.taggable),
        }


make_tag = TP.make_tag


@dataclass
class Identification:
    objects: list[Obj]
    best_fit_time: datetime | None = None
    time_flag: str | None = None
    checks: dict = field(default_factory=dict)


# ---------------------------------------------------------------- sky helpers
def _skyfield_times(times: list[datetime]):
    ts, _ = data.ephemeris()
    return ts.from_datetimes(times)


def altitudes(ra_deg: np.ndarray, dec_deg: np.ndarray, times: list[datetime], site: Site) -> np.ndarray:
    """Geometric altitude (deg) of fixed ICRS positions; shape (n_times, n_objects).

    Precession since J2000 (~0.4 deg) and refraction are ignored: both are far below the 3 deg margin."""
    t = _skyfield_times(times)
    lst = (np.atleast_1d(t.gast) * 15.0 + site.lon)[:, None]
    ha = np.radians(lst - np.asarray(ra_deg)[None, :])
    phi = math.radians(site.lat)
    dec = np.radians(np.asarray(dec_deg))[None, :]
    s = math.sin(phi) * np.sin(dec) + math.cos(phi) * np.cos(dec) * np.cos(ha)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def altitude_window(ra, dec, times: list[datetime], site: Site | None):
    """(worst-case = minimum, maximum) altitude over the sampled time window, per object."""
    if site is None:
        return None, None
    a = altitudes(np.atleast_1d(ra), np.atleast_1d(dec), times, site)
    return a.min(axis=0), a.max(axis=0)


def body_positions(key: str, times: list[datetime], site: Site | None):
    """Apparent (topocentric if site) RA/Dec (deg, ICRS-aligned) and altitude (deg or None) per time."""
    from skyfield.api import wgs84

    ts, eph = data.ephemeris()
    t = _skyfield_times(times)
    earth = eph["earth"]
    obs = earth + wgs84.latlon(site.lat, site.lon, elevation_m=site.elev_m) if site else earth
    app = obs.at(t).observe(eph[key]).apparent()
    ra, dec, _ = app.radec()
    alt = app.altaz()[0].degrees if site else None
    return np.atleast_1d(ra._degrees), np.atleast_1d(dec.degrees), (None if alt is None else np.atleast_1d(alt))


def body_magnitude(key: str, t: datetime, fallback: float) -> float:
    try:
        from skyfield.magnitudelib import planetary_magnitude

        ts, eph = data.ephemeris()
        tt = ts.from_datetime(t)
        m = float(planetary_magnitude(eph["earth"].at(tt).observe(eph[key])))
        return m if math.isfinite(m) else fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------- frame helpers
def project(sol: Solution, ra, dec):
    """Pixel coords (full res); NaN where the fitted projection cannot be trusted."""
    return safe_world2pix(sol.wcs, ra, dec, sol.width, sol.height)


def in_frame(sol: Solution, x, y, pad: float = 0.0):
    x, y = np.asarray(x), np.asarray(y)
    return np.isfinite(x) & np.isfinite(y) & (x >= -pad) & (y >= -pad) & (x <= sol.width - 1 + pad) & (y <= sol.height - 1 + pad)


def local_scale_deg(sol: Solution, x: float, y: float) -> float:
    a = sol.wcs.pixel_to_world([x, x + 10], [y, y])
    return float(a[0].separation(a[1]).deg) / 10.0


def fraction_inside(sol: Solution, x: float, y: float, r: float, n: int = 24) -> float:
    if r <= 1:
        return 1.0
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return float(np.mean(in_frame(sol, x + r * np.cos(ang), y + r * np.sin(ang))))


def bright_sources(sol: Solution, n: int = 150):
    d = sol.det
    o = d.order_by_peak()[:n]
    return d.x[o], d.y[o], d.flux[o]


# ---------------------------------------------------------------- main
def identify(sol: Solution, t_true: datetime, tol_min: float, site: Site | None, time_source: str,
             policy: TP.TagPolicy | None = None, min_confidence: int = 65) -> Identification:
    q = C.solve_quality(sol.grade)
    times = window(t_true, tol_min)
    epoch = t_true.year + (t_true.timetuple().tm_yday - 0.5) / 365.25
    psf = max(sol.psf_px, 2.0)
    lim = sol.lim_mag
    objs: list[Obj] = []
    cat = data.hipparcos()
    cra, cdec = cat.at_epoch(epoch)
    hip_index = cat.index_of()
    matched = set(int(h) for h in sol.match_hip)
    center_scale = local_scale_deg(sol, sol.width / 2, sol.height / 2)
    psf_arcmin = psf * center_scale * 60

    def alt_window(ra, dec):
        return altitude_window(ra, dec, times, site)

    # ---- named stars
    names = data.star_names()
    hips = [h for h, (_, v) in names.items() if v <= STAR_MAG_MAX and h in hip_index]
    if hips:
        ii = np.array([hip_index[h] for h in hips])
        x, y = project(sol, cra[ii], cdec[ii])
        inf = in_frame(sol, x, y)
        amin, amax = alt_window(cra[ii], cdec[ii])
        for k in np.nonzero(inf)[0]:
            if amax is not None and amax[k] < 0:
                continue
            h = hips[k]
            name, vmag = names[h]
            err = sol.local_error(x[k], y[k])
            p = C.position_certainty(err, psf)
            dd = 1.0 if h in matched else C.detectability(vmag, lim)
            m = C.horizon_margin(None if amin is None else float(amin[k]))
            objs.append(Obj(name, "star", C.combine(q, p, dd, m), float(x[k]), float(y[k]),
                            alt_min=None if amin is None else float(amin[k]), mag=vmag, level=TP.LEVEL_STAR,
                            ra=float(cra[ii[k]]), dec=float(cdec[ii[k]]), hips=(h,)))

    # ---- deep-sky objects
    dsos = data.deep_sky()
    if dsos:
        ra = np.array([o.ra for o in dsos])
        dec = np.array([o.dec for o in dsos])
        x, y = project(sol, ra, dec)
        inf = in_frame(sol, x, y)
        amin, amax = alt_window(ra, dec)
        for k in np.nonzero(inf)[0]:
            if amax is not None and amax[k] < 0:
                continue
            o = dsos[k]
            sc = local_scale_deg(sol, x[k], y[k])
            r_px = (o.size_arcmin / 60 / 2) / sc
            err = sol.local_error(x[k], y[k])
            p = C.position_certainty(err, max(r_px, psf))
            dd = C.detectability(C.extended_mag(o.vmag, o.size_arcmin, psf_arcmin), lim)
            m = C.frame_margin(fraction_inside(sol, x[k], y[k], r_px)) * C.horizon_margin(
                None if amin is None else float(amin[k]))
            objs.append(Obj(o.name, o.kind, C.combine(q, p, dd, m), float(x[k]), float(y[k]), radius_px=r_px,
                            alt_min=None if amin is None else float(amin[k]), mag=o.vmag, level=TP.LEVEL_CATALOG,
                            ra=o.ra, dec=o.dec))

    # ---- Sun, Moon, planets (time-dependent)
    bx, by, _ = bright_sources(sol)
    checks: dict = {}
    best_fit = None
    cap = C.EXIF_TIME_CAP if time_source not in ("config", "facet") else None
    mid = len(times) // 2
    for name, key, kind, mag_fb in PLANETS:
        ra, dec, alt = body_positions(key, times, site)
        x, y = project(sol, ra, dec)
        if not in_frame(sol, x[mid:mid + 1], y[mid:mid + 1])[0]:
            continue
        if alt is not None and alt.max() < 0:
            continue
        mag = body_magnitude(key, t_true, mag_fb)
        spread = float(np.nanmax(np.hypot(x - x[mid], y - y[mid]))) if np.isfinite(x).all() else 0.0
        err = math.hypot(sol.local_error(x[mid], y[mid]), spread)
        sc = local_scale_deg(sol, x[mid], y[mid])
        bright = mag <= BRIGHT_BODY_MAG
        if bright:  # Moon, Venus, Jupiter...: must be one of the brightest sources, within 0.5 deg
            cx_, cy_ = bx, by
            search = max(3 * err, 0.5 / sc)
        else:  # Uranus, Neptune, faint Mercury: any detection, but only within the fit error
            cx_, cy_ = sol.det.x, sol.det.y
            search = max(3 * err, 2 * psf)
        sep_deg = None
        found = False
        dist_j = None
        if len(cx_):
            dist = np.hypot(cx_ - x[mid], cy_ - y[mid])
            j = int(np.argmin(dist))
            dist_j = float(dist[j])
            sep_deg = dist_j * sc
            found = dist_j <= search
            checks[name] = {"pred_x": float(x[mid]), "pred_y": float(y[mid]), "src_x": float(cx_[j]),
                            "src_y": float(cy_[j]), "sep_deg": round(sep_deg, 3), "found": bool(found),
                            "bright": bright}
        size = max(psf, spread, (0.25 / sc) if key == "moon" else 0.0)
        p = C.position_certainty(err if not found else min(err, dist_j), size)
        dd = 1.0 if found else 0.5 * C.detectability(mag, lim)
        bonus = 1.1 if (found and bright) else 1.0
        m = C.horizon_margin(None if alt is None else float(alt.min()))
        conf = C.combine(q, p, dd, m, bonus, cap)
        objs.append(Obj(name, kind, conf, float(x[mid]), float(y[mid]), alt_min=None if alt is None else float(alt.min()),
                        sep_deg=sep_deg, mag=mag, level=TP.LEVEL_BODY, ra=float(ra[mid]), dec=float(dec[mid])))
        if key == "moon" and found:
            best_fit = moon_best_fit_time(sol, cx_[j], cy_[j], t_true, site)

    # ---- constellations
    lines = data.constellation_lines()
    for abbr, segs in lines.items():
        figure_hips = sorted({h for s in segs for h in s if h in hip_index})
        if len(figure_hips) < 2:
            continue
        ii = np.array([hip_index[h] for h in figure_hips])
        x, y = project(sol, cra[ii], cdec[ii])
        inf = in_frame(sol, x, y)
        if site is not None:
            amin, amax = alt_window(cra[ii], cdec[ii])
            inf &= amax >= 0
        if inf.sum() < 2:
            continue
        pos = {h: (x[n], y[n]) for n, h in enumerate(figure_hips)}
        n_matched = sum(1 for n, h in enumerate(figure_hips) if inf[n] and _star_seen(sol, h, matched, x[n], y[n], bx, by))
        conf = C.constellation_confidence(q, n_matched, len(figure_hips))
        figure = []
        figure_sky = []
        for a, b in segs:
            if a in pos and b in pos:
                ia, ib = hip_index[a], hip_index[b]
                figure_sky.append((float(cra[ia]), float(cdec[ia]), float(cra[ib]), float(cdec[ib])))
                if np.isfinite(pos[a]).all() and np.isfinite(pos[b]).all():
                    figure.append((*pos[a], *pos[b]))
        objs.append(Obj(data.CONSTELLATION_NAMES.get(abbr, abbr), "constellation", conf, None, None, figure=figure,
                        level=TP.LEVEL_CONSTELLATION, figure_sky=figure_sky))

    # ---- asterisms (named star groups): >= 80% of the member stars in frame and matched
    for aname, ahips in TP.ASTERISMS.items():
        members = [h for h in ahips if h in hip_index]
        if len(members) < 2:
            continue
        ii = np.array([hip_index[h] for h in members])
        x, y = project(sol, cra[ii], cdec[ii])
        inf = in_frame(sol, x, y)
        amin = None
        if site is not None:
            amin, amax = alt_window(cra[ii], cdec[ii])
            inf &= amax >= 0
        seen = [bool(inf[n]) and _star_seen(sol, h, matched, x[n], y[n], bx, by) for n, h in enumerate(members)]
        frac = sum(seen) / len(ahips)
        if frac < TP.ASTERISM_MIN_FRACTION:
            continue
        m = C.horizon_margin(None if amin is None else float(np.min(amin)))
        conf = C.combine(q, 1.0, frac, m)
        vis = [n for n in range(len(members)) if inf[n]]
        order = list(range(len(members))) + ([0] if aname in TP.ASTERISM_CLOSED else [])
        figure = [(float(x[a]), float(y[a]), float(x[b]), float(y[b])) for a, b in zip(order, order[1:])
                  if inf[a] and inf[b]]
        rr = np.radians(cra[ii])
        figure_sky = [(float(cra[ii[a]]), float(cdec[ii[a]]), float(cra[ii[b]]), float(cdec[ii[b]]))
                      for a, b in zip(order, order[1:])]
        objs.append(Obj(aname, "asterism", conf, float(np.mean(x[vis])), float(np.mean(y[vis])), figure=figure,
                        figure_sky=figure_sky,
                        level=TP.LEVEL_ASTERISM, mag=float(np.min(cat.vmag[ii])), hips=tuple(members),
                        alt_min=None if amin is None else float(np.min(amin)),
                        ra=float(np.degrees(np.arctan2(np.mean(np.sin(rr)), np.mean(np.cos(rr)))) % 360),
                        dec=float(np.mean(cdec[ii]))))

    _hierarchy(objs, cra, cdec, hip_index, policy or TP.TagPolicy(), min_confidence)

    # ---- time check
    time_flag = None
    if best_fit is not None and abs((best_fit - t_true).total_seconds()) > tol_min * 60:
        time_flag = "best_fit_outside_tolerance"
    objs.sort(key=lambda o: (-o.confidence, o.name))
    return Identification(objs, best_fit, time_flag, checks)


def _hierarchy(objs: list[Obj], cra, cdec, hip_index, policy: TP.TagPolicy, min_confidence: int) -> None:
    """Tag style, parent constellation, group membership and the taggable flag (in place)."""
    for o in objs:
        o.style = policy.tag_style
    need = [o for o in objs if o.level != TP.LEVEL_CONSTELLATION and o.ra is not None]
    if need:
        abbrs = TP.constellation_of(np.array([o.ra for o in need]), np.array([o.dec for o in need]))
        for o, a in zip(need, abbrs):
            o.parent = TP.constellation_tag(a, policy.tag_style) if a else None
            if o.level == TP.LEVEL_ASTERISM and len(o.hips) > 1:
                # an asterism spanning several constellations has no single parent
                ii = [hip_index[h] for h in o.hips]
                ab = set(TP.constellation_of(cra[ii], cdec[ii]))
                o.parent = TP.constellation_tag(ab.pop(), policy.tag_style) if len(ab) == 1 else None
    # membership: stars inside a cluster's radius (and fainter than it), asterism member stars
    clusters = sorted((o for o in objs if o.level == TP.LEVEL_CATALOG and o.kind == "cluster"
                       and o.radius_px > 0 and o.x is not None), key=lambda c: c.radius_px)
    asterisms = sorted((o for o in objs if o.level == TP.LEVEL_ASTERISM), key=lambda a: -a.confidence)
    for s in objs:
        if s.level != TP.LEVEL_STAR or s.x is None:
            continue
        groups = [c.tag for c in clusters if math.hypot(s.x - c.x, s.y - c.y) <= c.radius_px
                  and (s.mag is None or c.mag is None or s.mag > c.mag)]
        groups += [a.tag for a in asterisms if s.hips and s.hips[0] in a.hips]
        s.groups = groups
    TP.apply_policy(objs, policy, min_confidence)


def _star_seen(sol: Solution, hip: int, matched: set[int], x: float, y: float, bx, by) -> bool:
    """A figure star counts as matched if the fit used it, or (bright, saturated stars the fit may skip)
    a bright source sits within a few fit-errors of it."""
    if hip in matched:
        return True
    if len(bx) == 0:
        return False
    r = max(3 * sol.local_error(x, y), 2 * sol.psf_px)
    return bool(np.min(np.hypot(bx - x, by - y)) <= r)


def moon_best_fit_time(sol: Solution, sx: float, sy: float, t: datetime, site: Site | None,
                       span_h: float = 3.0, step_min: float = 1.0) -> datetime | None:
    """Time at which the ephemeris Moon best matches the detected Moon (it moves ~0.5 deg/h on the stars)."""
    sky = sol.wcs.pixel_to_world(sx, sy)
    n = int(2 * span_h * 60 / step_min) + 1
    grid = [t + timedelta(minutes=-span_h * 60 + i * step_min) for i in range(n)]
    ra, dec, _ = body_positions("moon", grid, site)
    r1, d1 = math.radians(sky.ra.deg), math.radians(sky.dec.deg)
    cosang = np.sin(np.radians(dec)) * math.sin(d1) + np.cos(np.radians(dec)) * math.cos(d1) * np.cos(np.radians(ra) - r1)
    k = int(np.argmax(cosang))
    if math.degrees(math.acos(min(1.0, cosang[k]))) > 1.0 or k in (0, n - 1):
        return None
    return grid[k]
