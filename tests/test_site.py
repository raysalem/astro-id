import json

import pytest

from astro_id.config import Site, parse_site, resolve
from astro_id.site import merge_config, parse_epqs, parse_latlon, parse_maps_url, run_site

DMS = "32°36'23.0\"N 116°19'26.0\"W"


@pytest.mark.parametrize("text,lat,lon", [
    ("32.6064, -116.3239", 32.6064, -116.3239),
    ("(32.6064,-116.3239)", 32.6064, -116.3239),
    ("32.6064 -116.3239", 32.6064, -116.3239),
    ("https://www.google.com/maps/@32.6064,-116.3239,15z", 32.6064, -116.3239),
    ("https://www.google.com/maps/place/Somewhere/@32.60,-116.32,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d32.6064!4d-116.3239",
     32.6064, -116.3239),
    ("https://maps.google.com/?q=32.6064,-116.3239", 32.6064, -116.3239),
    (DMS, 32.606389, -116.323889),
])
def test_parse_latlon(text, lat, lon):
    a, b = parse_latlon(text)
    assert a == pytest.approx(lat, abs=1e-5) and b == pytest.approx(lon, abs=1e-5)


@pytest.mark.parametrize("bad", ["", "hello", "95, 10", "https://www.google.com/maps/search/coffee"])
def test_parse_latlon_rejects(bad):
    with pytest.raises(ValueError):
        parse_latlon(bad)


def test_maps_url_prefers_pin_over_view():
    url = "https://www.google.com/maps/place/X/@10.0,20.0,15z/data=!3m1!4b1!8m2!3d11.5!4d21.5"
    assert parse_maps_url(url) == (11.5, 21.5)
    assert parse_maps_url("no coords here") is None


def test_parse_epqs():
    assert parse_epqs({"value": "1101.27"}) == pytest.approx(1101.27)
    assert parse_epqs({"value": -1000000}) is None
    assert parse_epqs({}) is None


def test_merge_keeps_time_settings():
    old = {"clock_offset": "+1h", "time_tolerance_min": 5, "timezone": "America/Denver", "extra": 1}
    out = merge_config(old, Site(1.0, 2.0, 3.0, "X"))
    assert out["clock_offset"] == "+1h" and out["time_tolerance_min"] == 5 and out["timezone"] == "America/Denver"
    assert out["site"] == {"name": "X", "lat": 1.0, "lon": 2.0, "elev_m": 3.0}
    assert out["extra"] == 1
    fresh = merge_config({}, Site(1.0, 2.0, 0.0, "Y"), offset="+2559d11h08m")
    assert fresh["clock_offset"] == "+2559d11h08m" and fresh["time_tolerance_min"] == 15


def _scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_run_site_non_interactive(tmp_path):
    rc = run_site(tmp_path, ask=_scripted(["https://www.google.com/maps/@32.6064,-116.3239,15z", "Dark Site",
                                           "+2559d11h08m", "y"]),
                  say=lambda m: None, open_browser=lambda s: None, elevation=lambda lat, lon: 1100.0)
    assert rc == 0
    cfg = json.loads((tmp_path / "astro-id.json").read_text())
    assert cfg["site"]["name"] == "Dark Site" and cfg["site"]["elev_m"] == 1100.0
    assert cfg["clock_offset"] == "+2559d11h08m"
    # second run keeps the offset (no offset question) and falls back to a typed elevation
    rc = run_site(tmp_path, ask=_scripted(["10, 20", "1500", "", "y"]), say=lambda m: None,
                  open_browser=lambda s: None, elevation=lambda lat, lon: None)
    cfg = json.loads((tmp_path / "astro-id.json").read_text())
    assert rc == 0 and cfg["site"] == {"name": "Dark Site", "lat": 10.0, "lon": 20.0, "elev_m": 1500.0}
    assert cfg["clock_offset"] == "+2559d11h08m"


def test_run_site_declined_writes_nothing(tmp_path):
    rc = run_site(tmp_path, ask=_scripted(["1, 2", "N", "", "n"]), say=lambda m: None,
                  open_browser=lambda s: None, elevation=lambda a, b: 5.0)
    assert rc == 1 and not (tmp_path / "astro-id.json").exists()


def test_config_resolution_flags_override(tmp_path):
    sub = tmp_path / "night" / "frames"
    sub.mkdir(parents=True)
    (tmp_path / "night" / "astro-id.json").write_text(json.dumps(
        {"clock_offset": "+1h", "time_tolerance_min": 10, "timezone": "UTC", "site": {"lat": 1, "lon": 2, "elev_m": 3}}))
    fc = resolve(sub, None, None, None)
    assert fc.time_source == "config" and fc.tolerance_min == 10 and fc.site.lat == 1 and fc.offset.seconds == 3600
    fc = resolve(sub, "+2h", "20m", "5,6,7")
    assert fc.offset.seconds == 7200 and fc.tolerance_min == 20
    assert (fc.site.lat, fc.site.lon, fc.site.elev_m) == (5, 6, 7)
    assert parse_site("1,2").elev_m == 0.0
    with pytest.raises(ValueError):
        parse_site("1")


def test_no_config_means_exif_time(tmp_path, monkeypatch):
    import astro_id.config as cfg

    monkeypatch.setattr(cfg, "find_config", lambda folder: None)
    fc = resolve(tmp_path, None, None, None)
    assert fc.time_source == "exif" and fc.offset.total_seconds() == 0 and fc.site is None
    assert fc.tolerance_min == 15
