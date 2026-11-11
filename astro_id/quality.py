"""Per-frame astro quality metrics for culling: star counts, focus (half-flux diameter), trailing
(elongation), sky background and noise, and an A/B/C quality grade.

Measured on the frame's own pixels (the Facet photo: the JPG, or a lone RAW), never on the solve source, so
frames of one session compare like for like. Stars are detected on the half-size image; size and shape are
measured on full-resolution stamps (JPG/TIF/PNG) around the brightest isolated, unsaturated stars.

- ``fwhm_px``: median half-flux diameter (HFD) in full-resolution pixels. For a Gaussian star the half-flux
  diameter equals the FWHM; for a defocused disc or donut it measures the disc, which is what focus needs.
- ``elongation``: median major/minor axis ratio from flux-weighted second moments (1.0 = round). Trailing
  and wind shake raise it.
Both use stars in the central part of the frame (radius <= 60 % of the half diagonal) when there are enough,
so wide-angle corner coma does not read as trailing.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .stars import detect

log = logging.getLogger("astro_id")

QUALITY_VERSION = 1
REDUCE = 2
DETECT_SMOOTH = 2.0          # reduced px: the star count and the sky mask use this scale
DETECT_SCALES = (2.0, 6.0, 16.0)   # reduced px: sharp points .. large defocused discs
MAX_STARS = 150              # brightest usable stars measured per detection scale
CENTER_RHO = 0.6             # central region used for focus/trailing when >= MIN_CENTER stars sit in it
MIN_CENTER = 15
MIN_MEASURED = 5
MIN_HFD_PX = 1.6            # full-res px: smaller is a hot pixel (dark frames), not a star
SATURATION_FRACTION = 0.97   # stamp peak >= this x the image maximum: saturated, not measured

# Absolute grade thresholds (full-resolution px). A session-relative verdict is the cull rule's job.
GRADE_A = {"fwhm_px": 6.0, "elongation": 1.35, "detected": 500}
GRADE_B = {"fwhm_px": 10.0, "elongation": 1.8, "detected": 100}


def _load_full(path: Path) -> np.ndarray | None:
    """Full-resolution grayscale (uint8 for 8-bit files) or None for a RAW (measured on the half-size image)."""
    from .imageio import RAW_EXT

    if path.suffix.lower() in RAW_EXT:
        return None
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("L"))


def star_shape(img: np.ndarray, x: float, y: float, r_start: float = 4.0, r_max: float = 90.0) -> dict | None:
    """Shape of one star near (x, y) in ``img``: {x, y, hfd, elongation, flux, peak, raw_peak, noise}, or None.

    Each round re-centres on the flux-weighted centroid and grows the aperture to 2.5 x the half-flux radius
    + 2 px (defocused discs need a big one); background = median of an annulus just outside it. The half-flux
    radius is interpolated on the signed cumulative flux sorted by radius (sub-pixel; clipping the noise at 0
    would bias it up)."""
    h, w = img.shape
    r_ap = r_start
    out = None
    for _ in range(6):
        rb = int(math.ceil(r_ap + 7))
        x0, x1 = int(round(x)) - rb, int(round(x)) + rb + 1
        y0, y1 = int(round(y)) - rb, int(round(y)) + rb + 1
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            return out
        st = img[y0:y1, x0:x1].astype(np.float64)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        rr = np.hypot(xx - x, yy - y)
        ann = st[(rr > r_ap + 2) & (rr <= r_ap + 7)]
        if ann.size < 8:
            return out
        bg = float(np.median(ann))
        noise = float(max(1.4826 * np.median(np.abs(ann - bg)), 0.5))
        inside = rr <= r_ap
        pos = np.where(inside & ((st - bg) > 3 * noise), st - bg, 0.0)   # noise would pull it to the middle
        if pos.sum() <= 0:
            return out
        x, y = float((pos * xx).sum() / pos.sum()), float((pos * yy).sum() / pos.sum())   # re-centre
        rr = np.hypot(xx - x, yy - y)
        inside = rr <= r_ap
        v = st[inside] - bg
        r_in = rr[inside]
        tot = float(v.sum())
        if tot <= 0:
            return out
        o = np.argsort(r_in)
        cum = np.cumsum(v[o])
        k = int(np.argmax(cum >= 0.5 * tot))
        rs = r_in[o]
        if k == 0:
            r50 = rs[0]
        else:
            c0, c1 = cum[k - 1], cum[k]
            r50 = rs[k - 1] + (rs[k] - rs[k - 1]) * ((0.5 * tot - c0) / max(c1 - c0, 1e-9))
        r50 = float(max(r50, 0.5))
        # flux-weighted second moments over the core (r <= 2 x r50, pixels > 3 sigma): noise far out
        # would make every star look round
        core = (rr <= max(2.0 * r50, 2.5)) & ((st - bg) > 3 * noise)
        elong = None
        if core.sum() >= 4:
            ww = st[core] - bg
            dx, dy = xx[core] - x, yy[core] - y
            mx, my = np.average(dx, weights=ww), np.average(dy, weights=ww)
            cxx = np.average((dx - mx) ** 2, weights=ww)
            cyy = np.average((dy - my) ** 2, weights=ww)
            cxy = np.average((dx - mx) * (dy - my), weights=ww)
            tr, det = cxx + cyy, cxx * cyy - cxy * cxy
            disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
            l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-6)
            elong = float(math.sqrt(l1 / l2))
        raw_peak = float(st[inside].max())
        out = {"x": x, "y": y, "hfd": 2 * r50, "elongation": elong, "flux": tot, "peak": raw_peak - bg,
               "raw_peak": raw_peak, "noise": noise}
        want = max(r_start, 2.5 * r50 + 2.0)
        if abs(want - r_ap) <= 0.1 * r_ap or r_ap >= r_max:
            break
        r_ap = min(r_max, want)
    return out


def _sky_background(gray: np.ndarray, det_x: np.ndarray, det_y: np.ndarray, cell: int) -> tuple[float, float]:
    """(background, noise) over the sky: cells holding at least 2 detections (foreground has none)."""
    h, w = gray.shape
    gh, gw = max(1, h // cell), max(1, w // cell)
    g = gray[: gh * cell, : gw * cell].reshape(gh, cell, gw, cell)
    cnt = np.zeros((gh, gw), int)
    ix = np.clip((det_x // cell).astype(int), 0, gw - 1)
    iy = np.clip((det_y // cell).astype(int), 0, gh - 1)
    np.add.at(cnt, (iy, ix), 1)
    med = np.median(g, axis=(1, 3))
    mad = np.median(np.abs(g - med[:, None, :, None]), axis=(1, 3)) * 1.4826
    sky = cnt >= 2
    if sky.sum() < 4:
        sky = np.ones_like(sky)
    return float(np.median(med[sky])), float(np.median(mad[sky]))


def grade(q: dict) -> str | None:
    f, e, n = q.get("fwhm_px"), q.get("elongation"), q.get("detected") or 0
    if f is None:
        return "C" if n else None
    e = e if e is not None else 1.0
    if f <= GRADE_A["fwhm_px"] and e <= GRADE_A["elongation"] and n >= GRADE_A["detected"]:
        return "A"
    if f <= GRADE_B["fwhm_px"] and e <= GRADE_B["elongation"] and n >= GRADE_B["detected"]:
        return "B"
    return "C"


def _r(v, nd=2):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def measure_arrays(gray: np.ndarray, full: np.ndarray | None, scale_arcsec: float | None = None,
                   max_value: float | None = None) -> dict:
    """Quality from a half-size grayscale ``gray`` and (optionally) the full-resolution ``full``.

    Sizes are reported in full-resolution pixels (``full`` shape, else 2 x ``gray``). Stars are detected at
    several smoothing scales (sharp points to large defocused discs) and measured with re-centring; the scale
    whose kernel matches the measured stars gives the medians (``detect_scale``)."""
    from scipy.spatial import cKDTree

    h, w = gray.shape
    R = float(REDUCE)                              # gray is the 2x2-binned frame
    cell = max(32, w // 75)
    dets = {sm: detect(gray, sigma=5.0, smooth=sm, cell=max(cell, int(10 * sm))) for sm in DETECT_SCALES}
    det = dets[DETECT_SMOOTH]
    bg, noise = _sky_background(gray, det.x, det.y, cell)
    q: dict = {"version": QUALITY_VERSION, "detected": int(len(det)), "matched": None, "measured": 0,
               "detect_scale": None,
               "fwhm_px": None, "fwhm_arcsec": None, "elongation": None, "roundness": None,
               "background": _r(bg, 1), "noise": _r(noise, 2), "grade": None}
    img = full if full is not None else gray
    to_img = R if full is not None else 1.0        # detection px -> measured image px
    to_full = 1.0 if full is not None else R       # measured image px -> full-resolution px
    vmax = float(max_value if max_value is not None else img.max())
    # Measure the brightest isolated stars found at each smoothing scale, then keep the scale that matches
    # the stars (matched filter: its kernel FWHM closest to their median half-flux diameter). Sharp frames
    # pick the finest scale; a badly defocused frame picks a coarse one, whose detections are whole discs
    # rather than fragments of their rims.
    per_scale: dict = {}
    for sm, d in dets.items():
        if len(d) == 0:
            continue
        x = (d.x + 0.5) * to_img - 0.5
        y = (d.y + 0.5) * to_img - 0.5
        size = 2 * np.sqrt(d.npix / np.pi) * to_img            # blob diameter in image px
        nn = (cKDTree(np.c_[x, y]).query(np.c_[x, y], k=2)[0][:, 1] if len(x) > 1
              else np.full(len(x), np.inf))
        order = d.order()
        order = order[len(order) // 50:]      # skip the brightest 2 %: bloated, near-saturated halos
        got = []
        for i in order:
            if len(got) >= MAX_STARS:
                break
            if nn[i] < max(1.5 * size[i], 12.0 / to_full):
                continue
            r = star_shape(img, float(x[i]), float(y[i]), r_start=max(4.0 / to_full, 0.6 * size[i]),
                           r_max=90.0 / to_full)
            if r is None or r["raw_peak"] >= SATURATION_FRACTION * vmax or r["peak"] < 8 * r["noise"]:
                continue
            if r["hfd"] * to_full < MIN_HFD_PX:   # hot pixel / noise spike, not a star image
                continue
            got.append(r)
        if len(got) >= MIN_MEASURED:
            per_scale[sm] = got
    if not per_scale:
        q["grade"] = grade(q)
        return q

    def mismatch(sm):
        hfd_det = float(np.median([r["hfd"] for r in per_scale[sm]])) / to_img   # detection px
        return abs(math.log(max(hfd_det, 0.5) / (2.3548 * sm)))
    best = min(per_scale, key=mismatch)
    kept = per_scale[best]
    q["measured"] = len(kept)
    q["detect_scale"] = best
    hfd_a = np.array([k["hfd"] for k in kept]) * to_full
    el_a = np.array([np.nan if k["elongation"] is None else k["elongation"] for k in kept])
    xs_a = np.array([k["x"] for k in kept]) * to_full
    ys_a = np.array([k["y"] for k in kept]) * to_full
    fw, fh = w * R, h * R
    rho = np.hypot(xs_a - (fw - 1) / 2, ys_a - (fh - 1) / 2) / (math.hypot(fw, fh) / 2)
    use = rho <= CENTER_RHO if (rho <= CENTER_RHO).sum() >= MIN_CENTER else np.ones(len(rho), bool)
    f = float(np.median(hfd_a[use]))
    e_ok = use & np.isfinite(el_a)
    e = float(np.median(el_a[e_ok])) if e_ok.sum() >= 3 else None
    q.update(fwhm_px=_r(f), elongation=_r(e, 3), roundness=_r(1.0 / e, 3) if e else None,
             fwhm_arcsec=_r(f * scale_arcsec, 1) if scale_arcsec else None)
    q["grade"] = grade(q)
    return q


def frame_quality(path: Path, scale_arcsec: float | None = None, matched: int | None = None) -> dict | None:
    """Quality metrics of one frame (``path`` is the Facet photo). None when the image cannot be read."""
    from .imageio import load_gray

    try:
        full = _load_full(path)
        if full is not None:   # half size by 2x2 mean: one decode instead of two
            h, w = (full.shape[0] // REDUCE) * REDUCE, (full.shape[1] // REDUCE) * REDUCE
            gray = full[:h, :w].reshape(h // REDUCE, REDUCE, w // REDUCE, REDUCE).mean(axis=(1, 3), dtype=np.float32)
        else:
            gray = load_gray(path, REDUCE)
        q = measure_arrays(gray, full, scale_arcsec, 255.0 if full is not None and full.dtype == np.uint8 else None)

    except Exception as exc:
        log.warning("%s: quality metrics failed (%s)", path.name, exc)
        return None
    q["matched"] = matched
    q["measured_on"] = "raw" if full is None else "jpg"
    log.info("%s: quality %s: %d stars, HFD %s px, elongation %s, background %s +- %s", path.name, q["grade"],
             q["detected"], q["fwhm_px"], q["elongation"], q["background"], q["noise"])
    return q
