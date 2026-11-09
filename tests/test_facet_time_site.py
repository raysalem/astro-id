"""--time (Facet-supplied true time), --site elevation lookup, timezone inference."""
import json
from datetime import timezone

import pytest
from pathlib import Path
P = Path(".")

import astro_id.config as cfg
from astro_id.config import infer_timezone, parse_time_flag, resolve
from astro_id.site import merge_config, Site
from test_synthetic import run_cli  # noqa: F401  (reuse the CLI runner + fixtures below)
from test_synthetic import frame_dir, stub_astap  # noqa: F401


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    cfg._ELEV_CACHE.clear()
    monkeypatch.setattr(cfg, "find_config", lambda folder: None)


def test_time_flag_with_offset_and_naive(tmp_path):
    t = parse_time_flag("2026-11-07T20:34:00-08:00", None)
    assert t.utcoffset().total_seconds() == -8 * 3600
    t = parse_time_flag("2026-11-07T20:34:00", "America/Los_Angeles")
    assert t.utcoffset().total_seconds() == -8 * 3600
    t = parse_time_flag("2026-07-07 20:34:00", "America/Los_Angeles")   # DST
    assert t.utcoffset().total_seconds() == -7 * 3600
    with pytest.raises(ValueError):
        parse_time_flag("yesterday", None)
    with pytest.raises(ValueError):
        parse_time_flag("2026-11-07T20:34:00", "Nowhere/Land")


def test_resolve_time_override_sets_facet_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "site_with_elevation", lambda s: s)
    fc = resolve(tmp_path, "+5h", None, "32.6,-116.3,100", "2026-11-07T20:34:00", "America/Los_Angeles")
    assert fc.time_source == "facet" and fc.time_override.hour == 20


def test_site_without_elevation_is_looked_up_and_cached(monkeypatch):
    calls = []
    import astro_id.site as site_mod

    monkeypatch.setattr(site_mod, "fetch_elevation", lambda lat, lon: calls.append((lat, lon)) or 1129.0)
    a = resolve(P, None, None, "32.6064,-116.3239")
    b = resolve(P, None, None, "32.60641,-116.32391")  # same rounded coordinate
    assert a.site.elev_m == 1129.0 and b.site.elev_m == 1129.0 and len(calls) == 1
    c = resolve(P, None, None, "32.6064,-116.3239,50")  # explicit wins, no fetch
    assert c.site.elev_m == 50 and len(calls) == 1


def test_site_elevation_failure_uses_zero(monkeypatch):
    import astro_id.site as site_mod

    monkeypatch.setattr(site_mod, "fetch_elevation", lambda lat, lon: None)
    fc = resolve(P, None, None, "10,20")
    assert fc.site.elev_m == 0.0


def test_timezone_inferred_from_site(tmp_path):
    assert infer_timezone(32.6064, -116.3239) == "America/Los_Angeles"
    fc = resolve(tmp_path, None, None, "32.6064,-116.3239,1100")
    assert fc.timezone == "America/Los_Angeles"


def test_null_timezone_in_json_is_inferred(tmp_path, monkeypatch):
    monkeypatch.undo()
    (tmp_path / "astro-id.json").write_text(json.dumps(
        {"clock_offset": "", "timezone": None, "site": {"lat": 32.6064, "lon": -116.3239, "elev_m": 1100}}))
    fc = resolve(tmp_path, None, None, None)
    assert fc.timezone == "America/Los_Angeles"


def test_no_site_no_timezone_falls_back_to_system(tmp_path):
    assert resolve(tmp_path, None, None, None).timezone is None  # true_time then uses the system zone


def test_merge_config_never_writes_null_timezone():
    out = merge_config({"timezone": None}, Site(32.6064, -116.3239, 1129, "TDS"))
    assert out["timezone"] == "America/Los_Angeles"
    out = merge_config({"timezone": "Europe/Paris"}, Site(32.6, -116.3, 1, "x"))
    assert out["timezone"] == "Europe/Paris"
    out = merge_config({}, Site(32.6, -116.3, 1, "x"), timezone="UTC")
    assert out["timezone"] == "UTC"


def test_cli_time_rejects_folder(frame_dir, capsys):
    from astro_id.cli import main

    assert main(["solve", str(frame_dir), "--time", "2026-11-08T05:15:00-08:00", "--json"]) == 2


def test_cli_time_overrides_exif(frame_dir, stub_astap, capsys, tmp_path):
    f = sorted(p for p in frame_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))[0]
    doc = run_cli(capsys, ["solve", str(f), "--time", "2026-11-08T05:15:00-08:00", "--site", "32.6064,-116.3239,1100",
                           "--overlay-dir", str(tmp_path / "o"), "--json", "--no-fallback"])
    fr = doc["results"][0]
    assert fr["time_source"] == "facet" and fr["true_time"] == "2026-11-08T05:15:00-08:00"
    assert fr["solved"]
