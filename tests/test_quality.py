"""Quality metrics on synthetic frames: focus (half-flux diameter), trailing (elongation), discs, sky."""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image

from astro_id.quality import frame_quality, grade, measure_arrays, star_shape

W, H = 2400, 1600


def render(fwhm=4.0, elong=1.0, angle=30.0, n=350, disc=None, bg=30.0, noise=3.0, seed=1):
    """(half-size gray, full uint8): Gaussian stars of ``fwhm`` stretched ``elong`` x along ``angle``,
    or uniform discs of diameter ``disc`` (a defocused lens)."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W), bg)
    sig = fwhm / 2.3548
    rad = 40 if disc is None else int(disc) // 2 + 3
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]
    a = math.radians(angle)
    u = xx * math.cos(a) + yy * math.sin(a)
    v = -xx * math.sin(a) + yy * math.cos(a)
    if disc is None:
        psf = np.exp(-0.5 * ((u / (sig * elong)) ** 2 + (v / sig) ** 2))
    else:
        psf = (np.hypot(xx, yy) <= disc / 2).astype(float)
    psf /= psf.sum()
    # jittered grid: isolated stars, about n of them
    step = max(2 * rad + 20, int(math.sqrt((W - 2 * rad) * (H - 2 * rad) / n)))
    pts = [(gx + rng.uniform(-5, 5), gy + rng.uniform(-5, 5))
           for gy in range(rad + 15, H - rad - 15, step) for gx in range(rad + 15, W - rad - 15, step)][:n]
    for x, y in pts:
        f = rng.uniform(1500, 4000) * (1 if disc is None else (disc / fwhm) ** 1.2)
        ix, iy = int(x), int(y)
        img[iy - rad:iy + rad + 1, ix - rad:ix + rad + 1] += f * psf
    img += rng.normal(0, noise, img.shape)
    full = np.clip(img, 0, 255).astype(np.uint8)
    gray = full.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3)).astype(np.float32)
    return gray, full


@pytest.mark.parametrize("fwhm", [3.0, 5.0, 10.0])
def test_fwhm_of_round_stars(fwhm):
    q = measure_arrays(*render(fwhm=fwhm), max_value=255)
    assert q["fwhm_px"] == pytest.approx(fwhm, rel=0.15)
    assert q["elongation"] < 1.15 and q["roundness"] > 0.87
    assert q["measured"] >= 50 and q["detected"] >= 200


def test_focus_orders_frames():
    sharp = measure_arrays(*render(fwhm=3.5), max_value=255)["fwhm_px"]
    soft = measure_arrays(*render(fwhm=8.0), max_value=255)["fwhm_px"]
    assert soft > 1.8 * sharp


@pytest.mark.parametrize("elong,low", [(2.0, 1.6), (3.0, 2.2)])
def test_trailing_is_elongation(elong, low):
    q = measure_arrays(*render(fwhm=4.0, elong=elong), max_value=255)
    assert low < q["elongation"] <= elong * 1.1


def test_defocused_discs_use_a_coarse_scale():
    # a uniform disc of diameter D has a half-flux diameter of D / sqrt(2)
    q = measure_arrays(*render(disc=60, n=120), max_value=255)
    assert q["detect_scale"] > 2.0
    assert q["fwhm_px"] == pytest.approx(60 / math.sqrt(2), rel=0.2)


def test_background_and_noise():
    q = measure_arrays(*render(bg=50.0, noise=4.0), max_value=255)
    assert q["background"] == pytest.approx(50, abs=2)
    assert q["noise"] == pytest.approx(4.0 / 2, rel=0.3)   # half-size image: 2x2 mean halves the noise


def test_empty_sky_has_no_stars():
    rng = np.random.default_rng(3)
    full = np.clip(30 + rng.normal(0, 3, (H, W)), 0, 255).astype(np.uint8)
    gray = full.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3)).astype(np.float32)
    q = measure_arrays(gray, full, max_value=255)
    assert q["detected"] < 5 and q["fwhm_px"] is None and q["grade"] in (None, "C")


def test_star_shape_edge_and_saturation():
    gray, full = render(fwhm=4.0)
    assert star_shape(full.astype(float), 2.0, 2.0) is None
    img = np.full((101, 101), 20.0)
    img[48:53, 48:53] = 255.0
    r = star_shape(img, 50, 50)
    assert r["raw_peak"] == 255.0   # the caller drops it as saturated


def test_grade():
    assert grade({"fwhm_px": 4.0, "elongation": 1.1, "detected": 3000}) == "A"
    assert grade({"fwhm_px": 8.0, "elongation": 1.1, "detected": 3000}) == "B"
    assert grade({"fwhm_px": 4.0, "elongation": 2.5, "detected": 3000}) == "C"
    assert grade({"fwhm_px": None, "detected": 0}) is None


def test_frame_quality_reads_a_file(tmp_path):
    gray, full = render(fwhm=4.0)
    p = tmp_path / "f.png"
    Image.fromarray(full).save(p)
    q = frame_quality(p, scale_arcsec=60.0, matched=123)
    assert q["matched"] == 123 and q["measured_on"] == "jpg"
    assert q["fwhm_arcsec"] == pytest.approx(q["fwhm_px"] * 60, abs=0.2)
    assert frame_quality(tmp_path / "missing.jpg") is None
