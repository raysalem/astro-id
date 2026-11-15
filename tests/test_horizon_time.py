"""B-E2: a site plus a wrong camera clock must not drop or lower objects the plate solve placed.

Stars, constellations, asterisms and deep-sky objects come from the solve, not the clock. The horizon is checked only
with a confirmed time and a site that the photo agrees with; a frame whose own matched stars were below the horizon at
the time used gets a warning and ``time_hint`` instead. Synthetic frames (ASTAP stubbed) cover the logic; the shipped
examples 04 and 05 are checked with the real ASTAP and skipped without it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import test_synthetic as T
from test_synthetic import stub_astap  # noqa: F401  (fixture)
from astro_id import confidence as C
from astro_id import data, solve
from astro_id.identify import SKY_BELOW_DEG, altitude_window, identify, sky_check
from astro_id.timeutil import format_offset, parse_offset, window

WRONG_CAMERA = "2019:11:05 18:17:40"   # example 05's camera clock: Leo is far below the horizon then
OFFSET = format_offset(T.T_TRUE.replace(tzinfo=None) - datetime(2019, 11, 5, 18, 17, 40))
SITE = "32.6064,-116.3239,1100"
BODY_KINDS = ("planet", "moon")


def _bodies(fr):
    return [o for o in fr["objects"] if o["kind"] in BODY_KINDS or o["name"] == "Sun"]


def _solved_by_plate(fr) -> dict:
    return {o["name"]: o["confidence"] for o in fr["objects"] if o["kind"] not in BODY_KINDS and o["name"] != "Sun"}


@pytest.fixture(scope="module")
def wrong_clock_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("wrongclock")
    T.render(d / "SYN0005.JPG", WRONG_CAMERA)   # the real 2026 dawn sky, stamped with a 2019 evening time
    return d


def _solve(capsys, monkeypatch, path, *extra):
    import astro_id.config as cfg

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)   # flags only
    return T.run_cli(capsys, ["solve", str(path), "--json", "--no-overlay", "--no-fallback", "--no-results-file",
                              "--no-quality", "--timezone", "America/Los_Angeles",
                              "--overlay-dir", str(path.parent / "out"), *extra])["results"][0]


# ---------------------------------------------------------------- end to end on a synthetic frame (no ASTAP)
def test_site_and_wrong_clock_keeps_every_solved_object(wrong_clock_dir, stub_astap, capsys, monkeypatch):
    p = wrong_clock_dir / "SYN0005.JPG"
    nosite = _solve(capsys, monkeypatch, p)
    site = _solve(capsys, monkeypatch, p, "--site", SITE)
    T._check_contract({"contract": 1, "results": [site]})
    assert site["time_source"] == "exif" and nosite["time_hint"] is None
    # every star, constellation, asterism and deep-sky object, at the same confidence as with no site at all
    assert len(_solved_by_plate(site)) > 20 and _solved_by_plate(site) == _solved_by_plate(nosite)
    assert any(o["kind"] == "star" and o["tier"] == "high" for o in site["objects"])
    assert any(o["kind"] == "constellation" and o["name"] == "Leo" for o in site["objects"])
    hint = site["time_hint"]
    assert hint["code"] == "sky_below_horizon" and hint["field_alt_deg"] < -20 and hint["stars_below_fraction"] > 0.9
    assert hint["time"].startswith("2019-11-05T18:17:40")
    assert any("sky was below the horizon" in w and "clock or the site is probably wrong" in w for w in site["warnings"])
    assert all(o["confidence"] <= C.EXIF_TIME_CAP for o in _bodies(site))   # planets per the caps


def test_confirmed_but_wrong_offset_is_caught_too(wrong_clock_dir, stub_astap, capsys, monkeypatch):
    """`+0m` says "the camera clock is right": the photo says otherwise, so bodies drop to candidate."""
    p = wrong_clock_dir / "SYN0005.JPG"
    nosite = _solve(capsys, monkeypatch, p, "--offset", "+0m")
    site = _solve(capsys, monkeypatch, p, "--offset", "+0m", "--site", SITE)
    assert site["time_source"] == "config" and site["time_hint"]["code"] == "sky_below_horizon"
    assert _solved_by_plate(site) == _solved_by_plate(nosite)
    assert all(o["confidence"] <= C.EXIF_TIME_CAP for o in _bodies(site))
    assert not any("no clock offset" in w for w in site["warnings"])
    assert any("sky was below the horizon" in w for w in site["warnings"])


def test_site_and_right_offset_unchanged(wrong_clock_dir, stub_astap, capsys, monkeypatch):
    p = wrong_clock_dir / "SYN0005.JPG"
    fr = _solve(capsys, monkeypatch, p, "--offset", OFFSET, "--site", SITE)
    assert fr["true_time"].startswith("2026-11-08T05:15:00") and fr["time_hint"] is None and fr["warnings"] == []
    tiers = {o["name"]: o["tier"] for o in _bodies(fr)}
    assert tiers.get("Jupiter") == "high" and tiers.get("Mars") == "high"
    # the field is high in the sky: the trusted horizon check leaves every plate-solved object as with no site
    nosite = _solve(capsys, monkeypatch, p, "--offset", OFFSET)
    assert _solved_by_plate(fr) == _solved_by_plate(nosite)


def test_no_site_wrong_clock_unchanged(wrong_clock_dir, stub_astap, capsys, monkeypatch):
    fr = _solve(capsys, monkeypatch, wrong_clock_dir / "SYN0005.JPG")
    assert fr["time_hint"] is None and not any("horizon at the recorded time" in w for w in fr["warnings"])
    assert any("no site" in w for w in fr["warnings"]) and any("no clock offset" in w for w in fr["warnings"])
    assert all(o["confidence"] <= C.EXIF_TIME_CAP for o in _bodies(fr))


# ---------------------------------------------------------------- the pieces
def test_sky_check_reads_the_matched_stars():
    cat = data.hipparcos()
    hip_index = cat.index_of()
    cra, cdec = cat.at_epoch(2026.85)
    from astro_id.config import Site

    site = Site(32.6064, -116.3239, 1100.0)
    leo = [h for h in (49669, 54872, 57632, 50583, 49583) if h in hip_index]   # Regulus, Zosma, Denebola, Algieba...
    sol = SimpleNamespace(center=T.CENTER)
    up = window(T.T_TRUE, 15)
    down = window(T.T_TRUE.replace(year=2019, month=11, day=5, hour=18, minute=17), 15)
    assert sky_check(sol, cra, cdec, hip_index, set(leo), up, None) is None
    ok = sky_check(sol, cra, cdec, hip_index, set(leo), up, site)
    assert ok["below_horizon"] is False and ok["field_alt_deg"] > 30 and ok["stars_below_fraction"] == 0
    bad = sky_check(sol, cra, cdec, hip_index, set(leo), down, site)
    assert bad["below_horizon"] is True and bad["field_alt_deg"] < -20 and bad["stars_below_fraction"] == 1
    # fewer than 3 matched stars: judged by the field centre
    few = sky_check(sol, cra, cdec, hip_index, set(leo[:2]), down, site)
    assert few["below_horizon"] is True and few["stars_below_fraction"] is None
    a, _ = altitude_window(cra[[hip_index[h] for h in leo]], cdec[[hip_index[h] for h in leo]], down, site)
    assert (a < SKY_BELOW_DEG).all()


def test_time_fit_reports_frames_whose_sky_was_below_the_horizon():
    from astro_id.facet.serve import time_fit

    hint = {"code": "sky_below_horizon", "time": "x", "field_alt_deg": -47.2, "stars_below_fraction": 1.0}
    tf = time_fit({"C:/a/B2.JPG": {"solved": True, "time_hint": hint}, "C:/a/B1.JPG": {"solved": True, "time_hint": hint},
                   "C:/a/B3.JPG": {"solved": True, "time_hint": None}, "C:/a/B4.JPG": {"solved": False}}, "")
    assert tf["sky_below"] == 2 and tf["sky_below_files"] == ["B1.JPG", "B2.JPG"] and tf["offset"] is None
    assert time_fit({}, "")["sky_below"] == 0


def test_panel_time_step_shows_the_clock_hint():
    html = (Path(__file__).parents[1] / "astro_id" / "facet" / "static" / "index.html").read_text(encoding="utf-8")
    assert "This photo's sky was below the horizon at the camera's time: the clock is probably wrong. Set the offset, or use Auto." in html
    assert "tf.sky_below" in html


# ---------------------------------------------------------------- the shipped examples, real ASTAP
def _astap_ready() -> bool:
    exe, db = solve.find_astap()
    return exe is not None and "w08" in solve.available_databases(db)


EXAMPLES = Path(__file__).parents[1] / "examples"
needs_astap = pytest.mark.skipif(not _astap_ready() or not (EXAMPLES / "astro-id.json").exists(),
                                 reason="ASTAP with the W08 database (and examples/) is not installed")


@pytest.fixture(scope="module")
def example_solves(tmp_path_factory):
    """Solve examples 04 and 05 once with the shipped config (site + offset); identify() is then re-run per case."""
    from astro_id import config as cfgmod
    from astro_id import pipeline

    d = tmp_path_factory.mktemp("ex")
    shutil.copy(EXAMPLES / "astro-id.json", d / "astro-id.json")
    cfg = json.loads((EXAMPLES / "astro-id.json").read_text(encoding="utf-8"))
    fc = cfgmod.resolve(d, None, None, None)
    out = {}
    for name in ("04-orion-rising.jpg", "05-dawn-jupiter-mars.jpg"):
        shutil.copy(EXAMPLES / name, d / name)
        ctx: dict = {}
        fr, _ = pipeline.process(d / name, None, None, None, None, False, False, quality=False, ctx=ctx)
        assert fr["time_source"] == "config" and fr["time_hint"] is None
        exif_time = datetime.fromisoformat(fr["true_time"]) - parse_offset(cfg["clock_offset"])
        out[name] = (ctx["sol"], datetime.fromisoformat(fr["true_time"]), exif_time, fc)
    return out


def _run(sol, t, site, source):
    ident = identify(sol, t, 15, site, source)
    return {o.name: o.confidence for o in ident.objects}, ident


@needs_astap
@pytest.mark.parametrize("name,n_all,bodies", [("04-orion-rising.jpg", 112, {"Uranus": 100}),
                                               ("05-dawn-jupiter-mars.jpg", 77, {"Jupiter": 100, "Mars": 100})])
def test_examples_keep_their_objects_with_a_wrong_clock(example_solves, name, n_all, bodies):
    sol, t_true, t_exif, fc = example_solves[name]
    right, ident = _run(sol, t_true, fc.site, "config")
    assert len(right) == n_all and {k: right.get(k) for k in bodies} == bodies and not ident.sky["below_horizon"]
    wrong, ident = _run(sol, t_exif, fc.site, "exif")            # the bug: 9 objects (04) and 0 (05) before the fix
    nosite, _ = _run(sol, t_exif, None, "exif")
    assert ident.sky["below_horizon"] and ident.sky["field_alt_deg"] < -20
    planets = {o.name for o in ident.objects if o.kind in BODY_KINDS or o.name == "Sun"}
    assert {k: v for k, v in wrong.items() if k not in planets} == {k: v for k, v in nosite.items() if k not in planets}
    assert len(wrong) >= n_all - len(bodies)
    assert all(wrong[k] <= C.EXIF_TIME_CAP for k in planets)
    # every plate-solved object of the right-time run is still there at the wrong time, same confidence
    solved = {k: v for k, v in right.items() if k not in bodies}
    assert {k: wrong.get(k) for k in solved} == solved
