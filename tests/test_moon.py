"""Moon features (astro_id/moon.py, docs/PLAN-moon.md): disc fit, phase/eclipse state, the physical ephemeris,
projection, the albedo fit, feature choice and the CLI. No network: the ephemeris tests skip when DE421 is not
already cached."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astro_id import cli
from astro_id import moon as M
from astro_id.moon_catalog import FEATURES

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- synthetic images
def synth(w=400, h=300, cx=200.0, cy=150.0, r=60.0, phase_k=None, sun_dir=0.0, eclipse=None, sky=8.0,
          lit=200.0, albedo=None, seed=0, blur=1.0):
    """RGB image of a disc. phase_k: terminator x-scale in [-1, 1] (1 full, 0 half, <0 crescent), lit side
    towards sun_dir (deg CCW from image right). eclipse: (ux, uy, ur, level) a red umbra disc. albedo(u, v):
    optional surface pattern on the unit disc (u right, v up)."""
    from scipy import ndimage

    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    u, v = (xx - cx) / r, -(yy - cy) / r
    rr = np.hypot(u, v)
    disc = rr <= 1.0
    a = math.radians(sun_dir)
    s = u * math.cos(a) + v * math.sin(a)          # towards the Sun
    t = -u * math.sin(a) + v * math.cos(a)
    lum = np.zeros((h, w))
    if phase_k is None:
        litm = disc
    else:
        edge = phase_k * np.sqrt(np.clip(1 - t * t, 0, 1))
        litm = disc & (s >= -edge)
    lum[litm] = lit
    if albedo is not None:
        lum = lum * np.where(disc, albedo(u, v), 1.0)
    img = np.stack([lum, lum, lum], axis=-1)
    if eclipse is not None:
        ux, uy, ur, level = eclipse
        inside = disc & (np.hypot(xx - ux, yy - uy) < ur)
        img[inside] = np.array([lit * level * 1.8, lit * level * 0.7, lit * level * 0.4])
    img = np.stack([ndimage.gaussian_filter(img[..., i], blur) for i in range(3)], axis=-1)
    rng = np.random.default_rng(seed)
    img = img + sky + rng.normal(0, 1.5, img.shape)
    return np.clip(img, 0, 255)


def test_full_disc_found():
    d = M.find_disc(synth())
    assert d is not None and abs(d.cx - 200) < 1 and abs(d.cy - 150) < 1 and abs(d.r - 60) < 1.2
    assert d.quality > 0.8 and d.coverage > 0.9
    assert M.disc_state(synth(), d).state == "full"


@pytest.mark.parametrize("k,state", [(0.5, "gibbous"), (0.0, "half"), (-0.6, "crescent")])
def test_phase_disc_found_from_the_limb(k, state):
    rgb = synth(phase_k=k, sun_dir=30.0)
    d = M.find_disc(rgb)
    assert d is not None and abs(d.cx - 200) < 1.5 and abs(d.cy - 150) < 1.5 and abs(d.r - 60) < 1.5
    st = M.disc_state(rgb, d)
    assert st.state == state
    assert 150 <= st.limb_span <= 230                  # a phase lights half the limb


def test_partial_arc_cut_by_the_frame():
    rgb = synth(cx=30.0, cy=150.0, r=60.0)             # half the disc is outside the frame
    d = M.find_disc(rgb)
    assert d is not None and abs(d.cx - 30) < 2 and abs(d.r - 60) < 2


def test_small_disc_in_a_wide_frame():
    rgb = synth(w=2000, h=1333, cx=1000.3, cy=600.7, r=33.0)
    d = M.find_disc(rgb)
    assert d is not None and abs(d.r - 33) < 1.2 and abs(d.cx - 1000.3) < 1


def test_red_eclipse_partial_and_total():
    rgb = synth(eclipse=(200 + 120, 150 + 30, 150, 0.15))
    d = M.find_disc(rgb)
    assert d is not None and abs(d.r - 60) < 2
    st = M.disc_state(rgb, d)
    assert st.state == "eclipse-partial" and st.red_ratio > 1.15
    rgb = synth(eclipse=(200, 150, 200, 0.15))
    d = M.find_disc(rgb)
    assert d is not None and M.disc_state(rgb, d).state == "eclipse-total"


def test_black_shadow_edge_is_an_eclipse_not_a_phase():
    # the umbra is black at this exposure, but its edge leaves a cap whose lit limb is far from 180 deg
    rgb = synth(eclipse=(200, 150 - 0.57 * 60 + 175, 175, 0.0))   # leaves a top cap with a ~110-deg lit limb
    d = M.find_disc(rgb)
    st = M.disc_state(rgb, d)
    assert st.limb_span < 140 and st.state == "eclipse-partial"


def test_blank_frame_has_no_disc():
    rng = np.random.default_rng(3)
    assert M.find_disc(np.clip(10 + rng.normal(0, 2, (300, 400, 3)), 0, 255)) is None


# ---------------------------------------------------------------- ephemeris maths
def test_meeus_example_53a():
    # Meeus, Astronomical Algorithms, example 53.a: 1992 April 12, 0h TD
    T = (2448724.5 - 2451545.0) / 36525
    l, b = M.optical_libration(133.167269, -3.229127, 0.004610, T)
    assert l == pytest.approx(-1.206, abs=0.002) and b == pytest.approx(4.194, abs=0.002)
    P = M.position_angle_axis(0.004610, 23.440636, 134.688469, b, T)
    assert P == pytest.approx(15.08, abs=0.05)        # 15.08 includes physical libration, ignored here


def _de421():
    from astro_id.data import cache_dir

    return (cache_dir() / "de421.bsp").exists()


@pytest.mark.skipif(not _de421(), reason="DE421 not cached (run astro-id setup); no network in this test")
@pytest.mark.parametrize("when,svs", [
    ("2026-11-24T12:00", (-2.698, -6.492, 347.241, 0.9979)),   # NASA SVS Dial-a-Moon, geocentric
    ("2026-11-20T06:00", (-7.241, -3.747, 337.819, 0.7666)),
])
def test_ephemeris_matches_nasa_svs(when, svs):
    e = M.physical_ephemeris(datetime.fromisoformat(when).replace(tzinfo=timezone.utc))
    assert e.l == pytest.approx(svs[0], abs=0.05)
    assert e.b == pytest.approx(svs[1], abs=0.05)
    assert e.P == pytest.approx(svs[2], abs=0.2)
    assert e.illuminated == pytest.approx(svs[3], abs=0.01)


@pytest.mark.skipif(not _de421(), reason="DE421 not cached (run astro-id setup); no network in this test")
def test_time_and_site_orient_a_level_camera(tmp_path):
    from astro_id.config import Site

    p = tmp_path / "m.jpg"
    Image.fromarray(synth(w=900, h=900, cx=450, cy=450, r=300.0).astype(np.uint8)).save(p)
    t = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)            # Moon up over California
    site = Site(32.6, -116.3, 1100.0)
    r = M.analyse(p, t, site, time_confirmed=False)
    e = M.physical_ephemeris(t, site)
    assert e.alt > 10
    assert r.orientation["method"] == "time+site, camera assumed level"
    assert r.orientation["certainty"] == M.ORIENT_EXIF_TIME
    assert r.orientation["pole_angle_deg"] == pytest.approx((-e.q + e.P) % 360, abs=0.01)


def test_parallactic_angle_signs():
    assert M.parallactic_angle(0.0, 10.0, 33.0) == pytest.approx(0.0, abs=1e-9)   # on the meridian
    assert M.parallactic_angle(30.0, 10.0, 33.0) > 0                              # west of it
    assert M.parallactic_angle(-30.0, 10.0, 33.0) < 0


# ---------------------------------------------------------------- projection
D = M.Disc(500.0, 400.0, 100.0, 0.3, 1.0, 1.0, 0.0)


def test_projection_centre_and_east_limb():
    x, y, z = M.project(np.array([0.0]), np.array([0.0]), D, 0.0, False)
    assert (x[0], y[0], z[0]) == pytest.approx((500.0, 400.0, 1.0))
    x, y, _ = M.project(np.array([0.0]), np.array([90.0]), D, 0.0, False)       # selenographic east limb
    assert (x[0], y[0]) == pytest.approx((600.0, 400.0))                         # right, pole up
    x, y, _ = M.project(np.array([0.0]), np.array([90.0]), D, 0.0, True)
    assert x[0] == pytest.approx(400.0)                                          # mirrored: left
    x, y, _ = M.project(np.array([90.0]), np.array([0.0]), D, 90.0, False)       # pole turned to the left
    assert (x[0], y[0]) == pytest.approx((400.0, 400.0))


def test_crisium_on_the_right_for_north_up():
    crisium = next(f for f in FEATURES if f[0] == "Mare Crisium")
    x, y, z = M.project(np.array([crisium[2]]), np.array([crisium[3]]), D, 0.0, False)
    assert x[0] > 570 and y[0] < 400 and z[0] > 0


def test_libration_brings_a_limb_feature_into_view():
    # a point 3 deg behind the east limb shows when the sub-Earth point moves 6 deg east
    lat, lon = np.array([0.0]), np.array([93.0])
    assert M.ortho(lat, lon, 0.0, 0.0)[2][0] < 0
    assert M.ortho(lat, lon, 6.0, 0.0)[2][0] > 0


def test_phi_from_north():
    assert M.phi_from_north(0.0, 347.0, False) == pytest.approx(347.0)
    assert M.phi_from_north(10.0, 20.0, False) == pytest.approx(30.0)
    assert M.phi_from_north(10.0, 20.0, True) == pytest.approx(350.0)


# ---------------------------------------------------------------- albedo fit
@pytest.mark.parametrize("phi", [0.0, 110.0, 250.0])
def test_albedo_fit_recovers_the_turn(phi):
    pole, east = M.axes(phi, False)

    def alb(u, v):                                  # u right, v up -> lunar (east, north) at angle phi
        x, y = u, -v                                # image axes
        e = x * east[0] + y * east[1]
        n = x * pole[0] + y * pole[1]
        ok = e * e + n * n < 1
        return np.where(ok, 0.45 + 0.55 * M.albedo_model(np.where(ok, e, 0), np.where(ok, n, 0)), 1)

    rgb = synth(r=50.0, albedo=alb)
    d = M.find_disc(rgb)
    st = M.disc_state(rgb, d)
    f = M.fit_orientation(rgb, d, st)
    assert f is not None
    assert abs((f[0] - phi + 180) % 360 - 180) <= 4
    assert f[2] > 0.5


def test_no_fit_below_the_minimum_size():
    rgb = synth(w=200, h=200, cx=100, cy=100, r=15.0)
    d = M.find_disc(rgb)
    assert M.fit_orientation(rgb, d, M.disc_state(rgb, d)) is None


# ---------------------------------------------------------------- features
def _feats(r):
    d = M.Disc(1000.0, 1000.0, r, 0.3, 1.0, 1.0, 0.0)
    return {f.name: f for f in M.select_features(d, None, 0.0, False, 1.0)}


def test_65px_disc_names_no_craters():
    f = _feats(32.5)
    assert not any(x.kind == "crater" for x in f.values())
    assert "Mare Imbrium" in f and "Tycho" not in f


def test_700px_disc_names_tycho_and_copernicus():
    f = _feats(350.0)
    assert {"Tycho", "Copernicus", "Plato", "Apollo 11"} <= set(f)
    assert f["Tycho"].confidence >= 80 and f["Tycho"].tag == "tycho"


def test_size_message():
    small = M.Disc(0, 0, 32.5, 0, 1, 1, 0)
    assert "65 px across" in M.size_message(small) and "about 245 px" in M.size_message(small)
    assert M.size_message(M.Disc(0, 0, 10, 0, 1, 1, 0)).endswith("the large craters about 245 px")


def test_catalog_is_curated_and_sane():
    kinds = {}
    for name, kind, lat, lon, dkm in FEATURES:
        kinds[kind] = kinds.get(kind, 0) + 1
        assert -90 <= lat <= 90 and -180 <= lon <= 180
    assert kinds["mare"] >= 20 and kinds["crater"] >= 40 and kinds["mountains"] >= 10 and kinds["landing site"] == 6
    tycho = next(f for f in FEATURES if f[0] == "Tycho")
    assert tycho[2] == pytest.approx(-43.3, abs=0.1) and tycho[3] == pytest.approx(-11.2, abs=0.1)


# ---------------------------------------------------------------- end to end
def _write(tmp_path, rgb, name="moon.jpg"):
    p = tmp_path / name
    Image.fromarray(rgb.astype(np.uint8)).save(p, quality=95)
    return p


def test_cli_small_disc_says_what_it_needs(tmp_path, capsys):
    p = _write(tmp_path, synth(w=800, h=600, cx=400, cy=300, r=30.0))
    assert cli.main(["moon", str(p), "--json", "--north-angle", "0"]) == 0
    out = json.loads(capsys.readouterr().out)
    r = out["moon"][0]
    assert r["found"] and "60 px across" in r["message"] and "245 px" in r["message"]
    assert not any(f["kind"] == "crater" for f in r["features"])
    assert (tmp_path / "moon_moon.jpg").exists()
    ann = r["annotations"]
    assert ann["version"] == 1 and ann["groups"][0]["id"] == "moon"
    assert all(it["group"] == "moon" and "x" in it and "score" in it for it in ann["items"])


def test_cli_large_disc_labels_craters_with_kind_tags(tmp_path, capsys):
    p = _write(tmp_path, synth(w=900, h=900, cx=450, cy=450, r=380.0))
    assert cli.main(["moon", str(p), "--json", "--north-angle", "0", "--tag-style", "kind", "--no-overlay"]) == 0
    r = json.loads(capsys.readouterr().out)["moon"][0]
    tags = {f["tag"] for f in r["features"]}
    assert "moon/tycho" in tags and "moon/mare imbrium" in tags
    assert r["orientation"]["method"] == "given"
    assert not (tmp_path / "moon_moon.jpg").exists()


def test_cli_exit_codes(tmp_path):
    assert cli.main(["moon", str(tmp_path / "missing.jpg")]) == 2
    assert cli.main(["moon", str(tmp_path)]) == 2                      # no photos
    p = _write(tmp_path, np.full((100, 100, 3), 10.0))
    assert cli.main(["moon", str(p), "--no-overlay"]) == 1             # no disc
    assert cli.main(["moon", str(p), "--time", "soon"]) == 2


@pytest.mark.skipif(not (ROOT / "examples" / "moon" / "05-nasa-svs-full-moon.jpg").exists(), reason="no example")
def test_nasa_example_without_time_fits_north_up():
    """The NASA SVS frame is drawn celestial-north up; its pole is at P = 347.2 deg. The albedo fit alone must
    find that within a few degrees."""
    r = M.analyse(ROOT / "examples" / "moon" / "05-nasa-svs-full-moon.jpg")
    assert r.found and r.state["state"] == "full"
    assert r.orientation["method"] == "albedo fit"
    assert abs((r.orientation["pole_angle_deg"] - 347.2 + 180) % 360 - 180) <= 4
    names = {f["name"] for f in r.features}
    assert {"Tycho", "Copernicus", "Mare Crisium"} <= names


@pytest.mark.skipif(not _de421(), reason="DE421 not cached (run astro-id setup); no network in this test")
@pytest.mark.skipif(not (ROOT / "examples" / "moon" / "05-nasa-svs-full-moon.jpg").exists(), reason="no example")
def test_nasa_full_moon_names_the_famous_features_with_its_time():
    """The NASA SVS full Moon (2026-11-24 12:00 UTC, north up) must name the best-known features at 80+."""
    t = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    r = M.analyse(ROOT / "examples" / "moon" / "05-nasa-svs-full-moon.jpg", t=t, north_angle=0.0)
    conf = {f["name"]: f["confidence"] for f in r.features}
    for name in ("Tycho", "Copernicus", "Plato", "Mare Crisium", "Mare Imbrium"):
        assert conf.get(name, 0) >= 80, (name, conf.get(name))
