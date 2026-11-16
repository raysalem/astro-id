"""Quality metrics on synthetic frames: focus (half-flux diameter), trailing (elongation), discs, sky."""
from __future__ import annotations

import math

import numpy as np
import pytest
from PIL import Image
from scipy.ndimage import gaussian_filter as ndi_gauss

from astro_id.cull import cull_session
from astro_id.quality import frame_quality, grade, measure_arrays, pick_scale, star_count, star_shape
from astro_id.stars import background_map

W, H = 2400, 1600


def render(fwhm=4.0, elong=1.0, angle=30.0, n=350, disc=None, bg=30.0, noise=3.0, seed=1, ramp=0.0,
           flux=(1500, 4000), pole=None, grain=0.0):
    """(half-size gray, full uint8): Gaussian stars of ``fwhm`` stretched ``elong`` x along ``angle``,
    or uniform discs of diameter ``disc`` (a defocused lens). ``ramp``: sky rises this many DN from top to
    bottom (twilight). ``pole`` = (x, y): trails are arcs around it (stretched tangentially, as an untracked
    frame around the celestial pole). ``grain``: extra noise of this sigma correlated over ~2 px (JPEG grain)."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W), bg, dtype=float) + np.linspace(0.0, ramp, H)[:, None]
    sig = fwhm / 2.3548
    rad = 40 if disc is None else int(disc) // 2 + 3
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]

    def gauss(angle_deg):
        a = math.radians(angle_deg)
        u = xx * math.cos(a) + yy * math.sin(a)
        v = -xx * math.sin(a) + yy * math.cos(a)
        p = np.exp(-0.5 * ((u / (sig * elong)) ** 2 + (v / sig) ** 2))
        return p / p.sum()
    if disc is None:
        psf = gauss(angle)
    else:
        psf = (np.hypot(xx, yy) <= disc / 2).astype(float)
        psf /= psf.sum()
    # jittered grid: isolated stars, about n of them
    step = max(2 * rad + 20, int(math.sqrt((W - 2 * rad) * (H - 2 * rad) / n)))
    pts = [(gx + rng.uniform(-5, 5), gy + rng.uniform(-5, 5))
           for gy in range(rad + 15, H - rad - 15, step) for gx in range(rad + 15, W - rad - 15, step)][:n]
    for x, y in pts:
        f = rng.uniform(*flux) * (1 if disc is None else (disc / fwhm) ** 1.2)
        ix, iy = int(x), int(y)
        p = psf if pole is None else gauss(math.degrees(math.atan2(y - pole[1], x - pole[0])) + 90)
        img[iy - rad:iy + rad + 1, ix - rad:ix + rad + 1] += f * p
    img += rng.normal(0, noise, img.shape)
    if grain:
        g = ndi_gauss(rng.normal(0, 1, img.shape), 1.0)
        img += g * (grain / g.std())
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


# ---------------------------------------------------------------- B-E3: twilight frames, solve scale, star evidence
def full_size(**kw):
    """Quality at the solve's scale rule for a 2400 px frame (reduce 1: the frame's own pixels)."""
    _, full = render(**kw)
    return measure_arrays(full.astype(np.float32), full, max_value=255, reduce=1)


def test_aligned_background_follows_a_ramp():
    img = np.add.outer(np.linspace(0, 160, 800), np.linspace(0, 40, 1200)).astype(np.float32)
    inner = (slice(64, -64), slice(64, -64))
    assert np.abs((img - background_map(img, 32, aligned=True))[inner]).max() < 0.05
    # the default mesh (kept for the solve) is shifted by up to half a cell: a ramp residual
    assert np.abs((img - background_map(img, 32))[inner]).max() > 1.0


@pytest.mark.parametrize("ramp", [120, 160])
def test_twilight_gradient_keeps_its_stars(ramp):
    # sky rising 120-160 DN top to bottom, faint stars, JPEG-like grain: main found 31 / 0 stars here (grade C)
    for q in (measure_arrays(*render(bg=80, ramp=ramp, flux=(400, 1200), noise=2.0, grain=2.0), max_value=255),
              full_size(bg=80, ramp=ramp, flux=(400, 1200), noise=2.0, grain=2.0)):
        assert q["detected"] >= 300 and q["measured"] >= 50
        assert q["fwhm_px"] == pytest.approx(4.0, rel=0.15) and q["elongation"] < 1.2
        assert q["grade"] == "B"     # 4 px stars, round; < 500 stars


def test_noise_grain_does_not_read_as_trailing():
    q = full_size(flux=(300, 900), grain=4.0)
    assert q["elongation"] < 1.15 and q["fwhm_px"] == pytest.approx(4.0, rel=0.15)


@pytest.mark.parametrize("elong,low,high", [(2.0, 1.6, 2.2), (3.0, 2.2, 3.3)])
@pytest.mark.parametrize("pole", [None, (1200, 800), (600, 400)])
def test_trailing_still_measured_at_full_size(elong, low, high, pole):
    # pole in the frame: trails are arcs around it; per-region coherence still sees them
    q = full_size(fwhm=4.0, elong=elong, pole=pole)
    assert low < q["elongation"] <= high


def test_defocus_still_measured_at_full_size():
    q = full_size(disc=60, n=120)
    assert q["detect_scale"] > 2.0 and q["fwhm_px"] == pytest.approx(60 / math.sqrt(2), rel=0.2)
    assert full_size(fwhm=8.0)["fwhm_px"] > 1.8 * full_size(fwhm=3.5)["fwhm_px"]


def test_cull_still_flags_trailed_and_defocused_frames():
    sharp = [full_size(fwhm=3.5, seed=s) for s in (1, 2, 3)]
    frames = {"sharp1": sharp[0], "sharp2": sharp[1], "sharp3": sharp[2],
              "trailed": full_size(fwhm=3.5, elong=3.0), "soft": full_size(fwhm=8.0),
              "twilight": full_size(fwhm=3.5, bg=80, ramp=160, flux=(400, 1200), noise=2.0, grain=2.0)}
    v = {k: r["decision"] for k, r in cull_session(frames)["frames"].items()}
    assert v["sharp1"] == v["sharp2"] == v["sharp3"] == "keep" and v["twilight"] == "keep"
    assert v["trailed"] in ("review", "reject") and v["soft"] == "review"


def test_star_evidence_uses_the_solve():
    q = {"fwhm_px": 2.0, "elongation": 1.1, "detected": 1, "matched": 924}
    assert star_count(q) == 924 and grade(q) == "A"
    assert star_count({"detected": 40, "matched": None}) == 40
    assert grade({"fwhm_px": 2.0, "elongation": 1.1, "detected": 40, "matched": None}) == "C"


def test_frame_quality_works_at_the_solve_scale(tmp_path):
    p = tmp_path / "small.png"           # 2400 px: under reduce_for's 6000 px, measured at its own pixels
    Image.fromarray(render(fwhm=3.0)[1]).save(p)
    q = frame_quality(p, matched=2000)
    assert q["detected"] >= 300 and q["fwhm_px"] == pytest.approx(3.0, rel=0.15)
    assert q["grade"] == "A"              # 345 stars detected, 2000 matched by the solve


def test_scale_choice_ignores_clumps_on_a_sharp_frame():
    # frame-7614 (61 MP, sharp, Milky Way), measured per scale with the aligned background (detection px, half size):
    # sm 2: 133 stars, HFD 3.2; sm 6: 140 "stars" HFD 13.5 (clumps); sm 16: 10 blobs HFD 33. The matched filter
    # alone picked sm 16 (HFD 66 full px) and the frame went to review for focus.
    hfd = {2.0: 3.2, 6.0: 13.46, 16.0: 32.9}
    assert pick_scale(hfd, {2.0: 133, 6.0: 140, 16.0: 10}) == 2.0
    # a real defocused frame (frame-7610): every scale reads the disc; the fine one has few whole stars
    assert pick_scale({2.0: 9.9, 6.0: 10.4, 16.0: 10.6}, {2.0: 33, 6.0: 150, 16.0: 55}) == 6.0
    # synthetic 60 px discs (half size): all scales agree, the matched filter picks the coarse one
    assert pick_scale({2.0: 20.7, 6.0: 20.6, 16.0: 21.3}, {2.0: 102, 6.0: 95, 16.0: 55}) > 2.0
