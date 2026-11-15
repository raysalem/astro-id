"""End-to-end tests on a synthetic night-sky frame rendered from Hipparcos through a known WCS.

ASTAP is not needed: its first-pass solution is replaced by the true WCS, deliberately perturbed, so the
refit, identification, scoring, overlay and JSON contract are all exercised.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from astropy.wcs import WCS
from PIL import Image

from astro_id import cli, data, solve
from astro_id.config import Site
from astro_id.identify import altitude_window, body_positions

W, H = 1800, 1200
SCALE = 0.025  # deg/px -> 45 x 30 deg field
CENTER = (146.0, 14.0)  # Leo: Jupiter and Mars are here at dawn on 2026-11-08
SITE = Site(32.6064, -116.3239, 1100.0)
T_TRUE = datetime(2026, 11, 8, 5, 15, tzinfo=ZoneInfo("America/Los_Angeles"))
FOCAL35 = 0.02 / math.tan(math.radians(SCALE))  # 36 mm / 1800 px pitch
FIXTURES = Path(__file__).parent / "fixtures"


def true_wcs() -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(CENTER)
    w.wcs.crpix = [(W + 1) / 2, (H + 1) / 2]
    a = math.radians(12.0)
    w.wcs.cd = np.array([[-math.cos(a), math.sin(a)], [math.sin(a), math.cos(a)]]) * SCALE
    w.pixel_shape = (W, H)
    return w


def render(path: Path, camera_time: str, shift: tuple[float, float] = (0.0, 0.0), exposure=None) -> None:
    """Synthetic frame; ``shift`` moves the sky by (dx, dy) pixels (a re-pointed or drifted frame)."""
    rng = np.random.default_rng(1)
    img = 20 + rng.normal(0, 2.0, (H, W))
    cat = data.hipparcos()
    ra, dec = cat.at_epoch(2026.85)
    w = true_wcs()
    w.wcs.crpix = [w.wcs.crpix[0] + shift[0], w.wcs.crpix[1] + shift[1]]
    sel = cat.vmag < 8.0
    x, y = w.wcs_world2pix(ra[sel], dec[sel], 0)
    mags = cat.vmag[sel]
    srcs = [(xx, yy, m) for xx, yy, m in zip(x, y, mags) if 0 < xx < W - 1 and 0 < yy < H - 1]
    for key, mag in (("jupiter barycenter", -2.3), ("mars", 1.0)):
        pra, pdec, _ = body_positions(key, [T_TRUE], SITE)
        px, py = w.wcs_world2pix(pra, pdec, 0)
        srcs.append((float(px[0]), float(py[0]), mag))
    yy, xx = np.mgrid[0:15, 0:15]
    for sx, sy, m in srcs:
        amp = 255 * 10 ** (-0.4 * (m - 3.5))
        x0, y0 = int(sx) - 7, int(sy) - 7
        if x0 < 0 or y0 < 0 or x0 + 15 > W or y0 + 15 > H:
            continue
        img[y0:y0 + 15, x0:x0 + 15] += amp * np.exp(-((xx + x0 - sx) ** 2 + (yy + y0 - sy) ** 2) / (2 * 1.3 ** 2))
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGB")
    exif = Image.Exif()
    ifd = exif.get_ifd(0x8769)
    ifd[36867] = camera_time
    ifd[37386] = FOCAL35
    ifd[41989] = int(round(FOCAL35))
    if exposure is not None:
        ifd[33434] = exposure
    im.save(path, "JPEG", quality=95, exif=exif)


@pytest.fixture(scope="module")
def frame_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("night")
    render(d / "SYN0001.JPG", "2026:11:08 05:15:00")
    (d / "SYN0001.ARW").write_bytes(b"")  # RAW twin must not be processed twice
    return d


def perturbed(_gray, _scale, _w, _h, log):
    w = true_wcs()
    w.wcs.crpix = [w.wcs.crpix[0] + 6.0, w.wcs.crpix[1] - 4.0]
    w.wcs.cd = w.wcs.cd * 1.004
    log.append("astap (test stub): ok")
    return w


@pytest.fixture
def stub_astap(monkeypatch):
    monkeypatch.setattr(solve, "solve_with_astap", perturbed)


def run_cli(capsys, args) -> dict:
    rc = cli.main(args)
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_refit_recovers_true_wcs(frame_dir, stub_astap):
    from astro_id.imageio import read_exif

    p = frame_dir / "SYN0001.JPG"
    sol = solve.solve_image(p, read_exif(p), use_fallback=False, epoch=2026.85)
    assert sol.solved and sol.matched >= 100 and sol.rms_px < 1.0 and sol.grade == "A"
    gx, gy = np.meshgrid(np.linspace(0, W - 1, 7), np.linspace(0, H - 1, 5))
    sky = true_wcs().pixel_to_world(gx.ravel(), gy.ravel())
    fx, fy = sol.wcs.world_to_pixel(sky)
    assert np.max(np.hypot(fx - gx.ravel(), fy - gy.ravel())) < 1.5
    fw, fh = sol.fov_deg()
    assert fw == pytest.approx(42.9, abs=0.3) and fh == pytest.approx(29.3, abs=0.3)  # 2 atan(n/2 tan s)


def test_safe_projection_rejects_far_sky():
    w = true_wcs()
    x, y = solve.safe_world2pix(w, [CENTER[0], CENTER[0] + 180], [CENTER[1], -CENTER[1]], W, H)
    assert np.isfinite(x[0]) and np.isnan(x[1])


def test_tolerance_window_takes_worst_case():
    # a star near the eastern horizon rises during the window: worst case is the start
    t = datetime(2026, 11, 7, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    from astro_id.timeutil import window

    amin, amax = altitude_window([88.79], [7.41], window(t, 15), SITE)  # Betelgeuse, rising at ~20:30
    a0, _ = altitude_window([88.79], [7.41], [t], SITE)
    assert amin[0] < a0[0] < amax[0]
    assert amax[0] - amin[0] == pytest.approx(7.0, abs=2.0)  # ~ 3.75 deg/15 min near rising for this dec
    assert altitude_window([0.0], [0.0], [t], None) == (None, None)


def _check_contract(doc: dict):
    stub = json.loads((FIXTURES / "contract_stub.json").read_text())
    assert doc["contract"] == stub["contract"] == 1
    frame_keys = set(stub["results"][0].keys())
    obj_keys = set(stub["results"][0]["objects"][0].keys())
    for fr in doc["results"]:
        assert set(fr.keys()) == frame_keys
        assert fr["solver"] in ("astap", "astrometry.net", "stack-copy", None)
        assert (fr["solver"] == "stack-copy") == (fr["stack_of"] is not None)
        assert fr["grade"] in ("A", "B", "C", None)
        assert fr["time_source"] in ("config", "exif", "facet")
        assert fr["time_flag"] in (None, "best_fit_outside_tolerance")
        assert isinstance(fr["stars"], int)
        if not fr["solved"]:
            assert fr["objects"] == [] and fr["error"]
        for o in fr["objects"]:
            assert set(o.keys()) == obj_keys
            assert isinstance(o["confidence"], int) and 0 <= o["confidence"] <= 100
            exp = "high" if o["confidence"] >= 80 else "likely" if o["confidence"] >= 65 else "candidate"
            assert o["tier"] == exp
            assert o["tag"] == o["tag"].lower() and "," not in o["tag"] and len(o["tag"]) <= 64
            assert o["kind"] in ("galaxy", "nebula", "cluster", "star", "planet", "moon", "constellation", "asterism",
                                 "other")
            assert o["level"] in (1, 2, 3, 4, 5) and isinstance(o["taggable"], bool)
            assert (o["level"] == 1) == (o["kind"] == "constellation")
            if o["level"] == 1:
                assert o["magnitude"] is None and o["parent"] is None
            if o["kind"] == "constellation":
                assert o["x"] is None and o["y"] is None
            else:
                assert isinstance(o["x"], float) and isinstance(o["y"], float)


def test_cli_json_contract_with_config_time(frame_dir, stub_astap, capsys, tmp_path):
    out_dir = tmp_path / "annotated"
    doc = run_cli(capsys, ["solve", str(frame_dir), "--offset", "+0m", "--time-tolerance", "15m",
                           "--site", "32.6064,-116.3239,1100", "--overlay-dir", str(out_dir), "--json", "--no-fallback"])
    _check_contract(doc)
    assert len(doc["results"]) == 1  # JPG+ARW pair counted once
    fr = doc["results"][0]
    assert fr["solved"] and fr["time_source"] == "config" and fr["true_time"] == "2026-11-08T05:15:00-08:00"
    names = {o["name"]: o for o in fr["objects"]}
    assert names["Jupiter"]["tier"] == "high" and names["Mars"]["tier"] in ("high", "likely")
    assert names["Regulus"]["confidence"] >= 80
    assert names["Leo"]["kind"] == "constellation" and names["Leo"]["confidence"] >= 65
    jx, jy = names["Jupiter"]["x"], names["Jupiter"]["y"]
    w = true_wcs()
    pra, pdec, _ = body_positions("jupiter barycenter", [T_TRUE], SITE)
    tx, ty = w.wcs_world2pix(pra, pdec, 0)
    assert math.hypot(jx - tx[0], jy - ty[0]) * SCALE < 0.1  # < 0.1 deg
    assert Path(fr["overlay"]).exists() and (out_dir / "astro-id-report.csv").exists()
    # tag hierarchy on a real identification: asterism found, its stars subsumed, stars off by default
    assert names["Leo"]["level"] == 1 and names["Jupiter"]["level"] == 2 and names["Regulus"]["level"] == 5
    assert names["Jupiter"]["parent"] == "leo" and names["Regulus"]["parent"] == "leo"
    sickle = names["Sickle of Leo"]
    assert sickle["kind"] == "asterism" and sickle["level"] == 4 and sickle["taggable"] and sickle["confidence"] >= 80
    assert names["Regulus"]["member_of"] == "sickle of leo" and not names["Regulus"]["taggable"]
    assert not any(o["taggable"] for o in fr["objects"] if o["level"] == 5)
    assert all(o["taggable"] for o in fr["objects"] if o["level"] in (1, 2))


def test_exif_only_time_caps_planets(frame_dir, stub_astap, capsys, tmp_path, monkeypatch):
    import astro_id.config as cfg

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)
    doc = run_cli(capsys, ["solve", str(frame_dir / "SYN0001.ARW"), "--overlay-dir", str(tmp_path), "--json",
                           "--no-overlay", "--no-fallback"])
    _check_contract(doc)
    fr = doc["results"][0]
    assert fr["file"].endswith("SYN0001.JPG")  # RAW argument mapped to its JPG twin
    assert fr["time_source"] == "exif" and fr["overlay"] is None
    planets = [o for o in fr["objects"] if o["kind"] in ("planet", "moon")]
    assert planets and all(o["confidence"] <= 64 and o["tier"] == "candidate" for o in planets)
    assert any(o["kind"] == "star" and o["tier"] == "high" for o in fr["objects"])  # fixed stars are not capped


def test_unsolved_frame_shape(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(solve, "solve_with_astap", lambda *a: None)
    img = (np.random.default_rng(0).normal(30, 2, (400, 600))).clip(0, 255).astype(np.uint8)
    Image.fromarray(img).save(tmp_path / "blank.jpg")
    doc = run_cli(capsys, ["solve", str(tmp_path / "blank.jpg"), "--json", "--no-overlay", "--no-fallback",
                           "--overlay-dir", str(tmp_path / "o")])
    _check_contract(doc)
    fr = doc["results"][0]
    assert fr["solved"] is False and fr["objects"] == [] and fr["error"]


def test_usage_errors_exit_nonzero(tmp_path):
    assert cli.main(["solve", str(tmp_path / "missing")]) == 2
    (tmp_path / "a.jpg").write_bytes(b"")
    assert cli.main(["solve", str(tmp_path), "--offset", "garbage"]) == 2


def test_results_file_written_by_default_and_merged(frame_dir, stub_astap, capsys, tmp_path):
    out_dir = tmp_path / "annotated"
    doc = run_cli(capsys, ["solve", str(frame_dir), "--offset", "+0m", "--site", "32.6064,-116.3239,1100",
                           "--overlay-dir", str(out_dir), "--json", "--no-overlay", "--no-fallback"])
    res = out_dir / "astro-id-results.json"
    assert res.exists() and not list(out_dir.glob("*.tmp"))
    on_disk = json.loads(res.read_text(encoding="utf-8"))
    assert on_disk == doc  # same document `--json` prints
    _check_contract(on_disk)
    # a second run of the same photo replaces its entry and keeps entries of other files
    stale = on_disk["results"] + [dict(on_disk["results"][0], file="C:/elsewhere/OTHER.JPG")]
    res.write_text(json.dumps({"contract": 1, "results": stale}), encoding="utf-8")
    run_cli(capsys, ["solve", str(frame_dir), "--offset", "+0m", "--site", "32.6064,-116.3239,1100",
                     "--overlay-dir", str(out_dir), "--json", "--no-overlay", "--no-fallback"])
    files = [r["file"] for r in json.loads(res.read_text(encoding="utf-8"))["results"]]
    assert len(files) == 2 and files.count(on_disk["results"][0]["file"]) == 1 and "C:/elsewhere/OTHER.JPG" in files


def test_no_results_file_flag(frame_dir, stub_astap, capsys, tmp_path):
    out_dir = tmp_path / "annotated"
    run_cli(capsys, ["solve", str(frame_dir), "--overlay-dir", str(out_dir), "--json", "--no-overlay",
                     "--no-fallback", "--no-results-file"])
    assert (out_dir / "astro-id-report.csv").exists() and not (out_dir / "astro-id-results.json").exists()


def test_session_run_writes_no_results_file(frame_dir, stub_astap, tmp_path):
    from astro_id.session import SessionOptions, run_session

    out_dir = tmp_path / "annotated"
    run_session([str(frame_dir / "SYN0001.JPG")], SessionOptions(overlay_dir=str(out_dir), no_overlay=True,
                                                                  no_fallback=True, offset="+0m"))
    assert not (out_dir / "astro-id-results.json").exists()   # the Facet add-on path never gets the new file


def test_warnings_without_offset_and_site(frame_dir, stub_astap, capsys, tmp_path, monkeypatch):
    import astro_id.config as cfg

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)
    fr = run_cli(capsys, ["solve", str(frame_dir / "SYN0001.JPG"), "--overlay-dir", str(tmp_path), "--json",
                          "--no-overlay", "--no-fallback", "--no-results-file"])["results"][0]
    assert any("no clock offset" in w for w in fr["warnings"])
    assert any("no site" in w and "no default site" in w for w in fr["warnings"])
    # offset + site given: neither warning
    fr = run_cli(capsys, ["solve", str(frame_dir / "SYN0001.JPG"), "--offset", "+0m", "--site", "32.6,-116.3,1100",
                          "--overlay-dir", str(tmp_path), "--json", "--no-overlay", "--no-fallback",
                          "--no-results-file"])["results"][0]
    assert fr["warnings"] == []


def test_planets_without_site_are_capped_with_site_unchanged(frame_dir, stub_astap, capsys, tmp_path, monkeypatch):
    import astro_id.config as cfg
    from astro_id import confidence as C

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)
    base = ["solve", str(frame_dir / "SYN0001.JPG"), "--offset", "+0m", "--overlay-dir", str(tmp_path), "--json",
            "--no-overlay", "--no-fallback", "--no-results-file"]
    nosite = run_cli(capsys, base)["results"][0]
    bodies = [o for o in nosite["objects"] if o["name"] in ("Sun", "Moon") or o["kind"] == "planet"]
    assert any(o["name"] == "Jupiter" for o in bodies)
    for o in bodies:
        cap = C.NO_SITE_MOON_CAP if o["name"] == "Moon" else C.NO_SITE_CAP
        assert o["confidence"] <= cap and o["tier"] != "high", o
    assert next(o for o in bodies if o["name"] == "Jupiter")["tier"] == "likely"
    assert any("no site" in w and "capped at 79" in w for w in nosite["warnings"])
    assert any(o["kind"] == "star" and o["tier"] == "high" for o in nosite["objects"])   # stars unaffected
    withsite = run_cli(capsys, base + ["--site", "32.6064,-116.3239,1100"])["results"][0]
    assert next(o for o in withsite["objects"] if o["name"] == "Jupiter")["tier"] == "high"   # unchanged


def test_facet_session_site_hint_keeps_full_confidence(frame_dir, stub_astap, tmp_path):
    """The add-on passes a site (Facet GPS hint or the folder config): no cap, no no-site warning."""
    from astro_id.session import SessionOptions, run_session

    p = str(frame_dir / "SYN0001.JPG")
    out = run_session([p], SessionOptions(offset="+0m", no_fallback=True, overlay_dir=str(tmp_path / "ov"),
                                          include_results=True, hints={p: {"site": "32.6064,-116.3239,1100"}}))
    fr = out["results"][p]
    assert next(o for o in fr["objects"] if o["name"] == "Jupiter")["tier"] == "high"
    assert not any("no site" in w for w in fr.get("warnings", []))
