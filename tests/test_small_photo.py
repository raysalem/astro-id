"""B-E1: small (downscaled / web-sized) photos must plate-solve like the full-size frame they came from.

Cause: every image was halved before star detection and before the ASTAP crops (a step meant for 60 MP
frames), so a 2400 px copy reached ASTAP as a ~300"/px image (or that image upsampled 2x), and the
faint stars ASTAP needs were gone. The preparation tests below need no ASTAP; the end-to-end ones are
skipped when ASTAP and its W08 database are not installed.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from astropy.wcs import WCS
from PIL import Image

from astro_id import data, solve
from astro_id.imageio import ExifInfo, read_exif

A7R4 = (9504, 6336)


def exif_for(long_edge: int, focal: float = 20.0) -> ExifInfo:
    w = long_edge
    h = round(long_edge * A7R4[1] / A7R4[0])
    return ExifInfo(None, focal, focal, w, h, 10.0)


def prepared(long_edge: int, focal: float = 20.0):
    """(reduce, full-res scale deg/px, [(label, image shape, fov)]) as solve_image would hand them to ASTAP."""
    ex = exif_for(long_edge, focal)
    r = solve.reduce_for(ex.width, ex.height)
    gray = np.zeros((ex.height // r, ex.width // r), np.float32)
    sc = solve.pixel_scale_deg(ex, ex.width, ex.height)
    atts = [(lab, img.shape, fov, tol) for lab, img, fov, _, tol in solve.astap_attempts(gray, sc, ex.width, ex.height)]
    return r, sc, atts


# ---------------------------------------------------------------- preparation (no ASTAP)
def test_full_size_frames_keep_the_half_scale_path():
    assert solve.reduce_for(*A7R4) == 2
    assert solve.reduce_for(6000, 4000) == 2  # 24 MP cameras: unchanged
    assert solve.reduce_for(4000, 6000) == 2  # portrait


@pytest.mark.parametrize("long_edge", [1200, 1600, 2000, 2400, 2800, 3000, 3200, 4800, 5999])
def test_small_frames_are_worked_at_their_own_resolution(long_edge):
    assert solve.reduce_for(long_edge, round(long_edge / 1.5)) == 1


@pytest.mark.parametrize("long_edge", [2000, 2400, 2800, 3000, 3200, 4000, 4800, 5600, 6000, 7200, 9504])
def test_astap_never_gets_upsampled_pixels(long_edge):
    """Every ASTAP image is at most as fine as the work image: no blocky 2x upsample of a halved frame."""
    r, sc, atts = prepared(long_edge)
    work_scale = r * sc
    for label, shape, fov, tol in atts:
        if "/up2" in label:  # the one deliberate (smooth) 2x upsample of a coarse work image
            assert solve.coarse_work_image(sc, r) and tol == solve.LOOSE_TOLERANCE
            assert fov / shape[0] == pytest.approx(work_scale / 2, rel=0.05)
            continue
        assert fov / shape[0] >= 0.95 * work_scale, (long_edge, label)


@pytest.mark.parametrize("long_edge", [1600, 2000, 2400, 2800, 3200, 6000, 6400])
def test_coarse_frames_get_the_loose_tolerance_attempts_early(long_edge):
    """Downscaled copies (and halved 24 MP frames) at 20 mm: right after the usual first attempt, the same
    30 deg field at the work scale and smoothly upsampled 2x, both with ASTAP -t 0.014."""
    r, sc, atts = prepared(long_edge)
    assert solve.coarse_work_image(sc, r)
    assert atts[0][3] is None
    assert [a[0] for a in atts[1:3]] == [f"c30/b{r}/t", "c30/up2/t"]
    assert [a[3] for a in atts[1:3]] == [solve.LOOSE_TOLERANCE] * 2
    assert all(a[3] is None for a in atts[3:])


@pytest.mark.parametrize("long_edge", [2000, 2400, 2800, 3200, 4000])
def test_small_wide_field_frames_reach_astap_at_native_scale(long_edge):
    """The first attempt is the central 30 deg at the photo's own pixel scale (from ~4600 px up the
    pixels are fine enough that the usual ~120"/px binning applies)."""
    r, sc, atts = prepared(long_edge)
    label, shape, fov, tol = atts[0]
    assert r == 1 and label == "c30/b1" and tol is None
    assert fov == pytest.approx(30.0, abs=0.2)
    assert shape[0] == pytest.approx(solve.npx_for_span(30, sc), abs=1)


def test_work_resolution_never_falls_below_a_3000_px_copy():
    """Monotonic preparation: pixels across the 30 deg crop grow with size, and the halving step
    (>= 6000 px) never leaves less than a 3000 px copy gets."""
    def work_px(L):
        r, sc, _ = prepared(L)
        return solve.npx_for_span(30, sc) // r

    sizes = list(range(1600, 6000, 100))
    px = [work_px(L) for L in sizes]
    assert px == sorted(px)
    floor = work_px(3000)
    assert all(work_px(L) >= floor for L in range(6000, 9600, 250))


def test_full_size_20mm_attempts_are_unchanged():
    """Full-size A7R IV frames go through exactly the attempts they did before the fix."""
    r, sc, atts = prepared(9504)
    assert r == 2
    assert [a[0] for a in atts] == ["c30/b3", "c30/b4", "c40/b4", "c30-up", "c30-down", "c30-left", "c30-right",
                                    "c30/b2"]
    assert [a[1][0] for a in atts] == [943, 707, 960, 707, 707, 707, 707, 1414]  # main eb237e8, measured
    assert all(a[3] is None for a in atts)  # ASTAP default tolerance
    assert not solve.coarse_work_image(sc, r)


@pytest.mark.parametrize("long_edge", [2400, 9504])
def test_narrow_field_path_unchanged(long_edge):
    """135 mm: the frame is solved directly (no 30 deg crops), binned to ~1500 then ~1000 px."""
    r, sc, atts = prepared(long_edge, focal=135.0)
    assert [a[0] for a in atts] == ["full", "full-bin"]
    assert all(a[3] is None for a in atts)
    side = round(long_edge * A7R4[1] / A7R4[0])
    assert atts[0][1][0] == max(64, round(side / max(1, side / 1500)))


def test_small_image_hint():
    assert solve.small_image_hint(1600, 1067) == (
        "too few stars at this size (1600 px long edge); use a larger copy (at least 2400 px)")
    assert solve.small_image_hint(2399, 1600) != ""
    assert solve.small_image_hint(2400, 1600) == ""
    assert solve.small_image_hint(9504, 6336) == ""


def test_clipped_stars_rank_by_flux_on_own_resolution_images():
    """8-bit images worked at their own pixels clip many stars at 252-255: those rank by flux (a V 1.8 star
    had fallen to rank ~190 by JPEG-noise peaks). Halved frames keep plain peak order."""
    from types import SimpleNamespace

    from astro_id.identify import bright_sources
    from astro_id.stars import Detections

    x = np.arange(4.0)
    peak = np.array([255.0, 253.0, 254.0, 120.0])
    flux = np.array([10.0, 900.0, 20.0, 5000.0])  # [1] is the bright star, [3] a big diffuse blob
    d = Detections(x, x, flux, peak, np.ones(4), 1.0, 0.0)
    assert list(d.order_by_peak()) == [0, 2, 1, 3]
    assert list(d.order_by_brightness()) == [1, 2, 0, 3]
    own = SimpleNamespace(det=d, reduce=1)
    halved = SimpleNamespace(det=d, reduce=2)
    assert list(bright_sources(own, 2)[0]) == [1.0, 2.0]
    assert list(bright_sources(halved, 2)[0]) == [0.0, 2.0]


def test_tiny_unsolved_photo_says_use_a_larger_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(solve, "solve_with_astap", lambda *a: None)
    img = np.random.default_rng(0).normal(30, 2, (533, 800)).clip(0, 255).astype(np.uint8)
    p = tmp_path / "tiny.jpg"
    Image.fromarray(img).save(p)
    sol = solve.solve_image(p, read_exif(p), use_fallback=False)
    assert not sol.solved and "use a larger copy" in sol.error


# ---------------------------------------------------------------- end to end with ASTAP (skipped without it)
def _astap_ready() -> bool:
    exe, db = solve.find_astap()
    return exe is not None and "w08" in solve.available_databases(db)


needs_astap = pytest.mark.skipif(not _astap_ready(), reason="ASTAP with the W08 database is not installed")
CENTER = (60.0, 45.0)  # Perseus / Auriga
TILT = math.radians(8.0)


def _render_20mm(path, long_edge: int, seed: int = 3) -> WCS:
    """A 20 mm full-frame night sky rendered at ``long_edge`` px: stars to V 8.5, PSF ~ 3 px at 9504 px
    (so ~0.8 px at 2400), sky noise, saved as an 8-bit JPEG like a downscaled camera file."""
    ex = exif_for(long_edge)
    W, H = ex.width, ex.height
    s = solve.pixel_scale_deg(ex, W, H)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(CENTER)
    w.wcs.crpix = [(W + 1) / 2, (H + 1) / 2]
    # tan-plane CD so that the rectilinear lens maps pixel -> tan(angle) linearly
    k = math.degrees(math.tan(math.radians(s)))
    w.wcs.cd = np.array([[-math.cos(TILT), math.sin(TILT)], [math.sin(TILT), math.cos(TILT)]]) * k
    w.pixel_shape = (W, H)
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(2026.85)
    sel = cat.vmag < 8.5
    x, y = w.wcs_world2pix(ra[sel], dec[sel], 0)
    m = cat.vmag[sel]
    ok = np.isfinite(x) & (x > 2) & (y > 2) & (x < W - 3) & (y < H - 3)
    rng = np.random.default_rng(seed)
    img = 25 + rng.normal(0, 2.0, (H, W))
    sig = max(0.5, 1.3 * long_edge / 9504)
    r = 4
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    for sx, sy, mag in zip(x[ok], y[ok], m[ok]):
        # summed counts: V 8 is ~3 sigma at the peak at 2400 px; a downscale keeps the per-pixel sky but
        # spreads each star's light over (L / 2400)^2 more pixels
        flux = 24 * 10 ** (-0.4 * (mag - 8.0)) * (long_edge / 2400) ** 2
        x0, y0 = int(round(sx)), int(round(sy))
        g = np.exp(-((xx + x0 - sx) ** 2 + (yy + y0 - sy) ** 2) / (2 * sig ** 2))
        ys, xs = slice(max(0, y0 - r), min(H, y0 + r + 1)), slice(max(0, x0 - r), min(W, x0 + r + 1))
        gy, gx = slice(ys.start - (y0 - r), ys.stop - (y0 - r)), slice(xs.start - (x0 - r), xs.stop - (x0 - r))
        img[ys, xs] += flux * g[gy, gx] / g.sum()
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGB")
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[37386] = 20.0
    ifd[41989] = 20
    im.save(path, "JPEG", quality=90, exif=exif)
    return w


@needs_astap
@pytest.mark.parametrize("long_edge", [2000, 2400, 3200])
def test_small_20mm_frame_solves_with_astap(tmp_path, long_edge):
    p = tmp_path / f"syn{long_edge}.jpg"
    truth = _render_20mm(p, long_edge)
    sol = solve.solve_image(p, read_exif(p), use_fallback=False, epoch=2026.85)
    assert sol.solved, sol.attempts
    c = truth.pixel_to_world((sol.width - 1) / 2, (sol.height - 1) / 2)
    ra, dec = sol.center
    assert math.hypot((ra - c.ra.deg) * math.cos(math.radians(dec)), dec - c.dec.deg) < 0.2
    assert sol.grade in ("A", "B")
