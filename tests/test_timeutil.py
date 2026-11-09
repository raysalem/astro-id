from datetime import datetime, timedelta

import pytest

from astro_id.timeutil import format_offset, parse_offset, parse_tolerance, true_time, window


@pytest.mark.parametrize("text,expected", [
    ("+2559d11h08m", timedelta(days=2559, hours=11, minutes=8)),
    ("2559d11h08m", timedelta(days=2559, hours=11, minutes=8)),
    ("-1h30m", -timedelta(hours=1, minutes=30)),
    ("90m", timedelta(minutes=90)),
    ("+45s", timedelta(seconds=45)),
    ("2559d 11:08", timedelta(days=2559, hours=11, minutes=8)),
    ("-00:30", -timedelta(minutes=30)),
    ("", timedelta(0)),
    (None, timedelta(0)),
])
def test_parse_offset(text, expected):
    assert parse_offset(text) == expected


@pytest.mark.parametrize("bad", ["abc", "+12x", "d", "1h2x"])
def test_parse_offset_rejects(bad):
    with pytest.raises(ValueError):
        parse_offset(bad)


def test_offset_round_trip():
    td = timedelta(days=2559, hours=11, minutes=8)
    assert format_offset(td) == "+2559d11h08m"
    assert parse_offset(format_offset(-td)) == -td


@pytest.mark.parametrize("text,minutes", [("15m", 15), ("15", 15), (15, 15), ("1h", 60), ("90s", 1.5), ("2 min", 2)])
def test_parse_tolerance(text, minutes):
    assert parse_tolerance(text) == pytest.approx(minutes)


def test_true_time_applies_offset_in_named_zone():
    # camera 2019-11-05 09:26 + 2559d11h08m = 2026-11-07 20:34 PST (standard time, -08:00)
    t = true_time(datetime(2019, 11, 5, 9, 26), parse_offset("+2559d11h08m"), "America/Los_Angeles")
    assert t.isoformat() == "2026-11-07T20:34:00-08:00"


def test_window_spans_tolerance_and_includes_center():
    t = datetime(2026, 11, 7, 20, 34)
    w = window(t, 15)
    assert w[0] == t - timedelta(minutes=15) and w[-1] == t + timedelta(minutes=15)
    assert t in w
    assert window(t, 0) == [t]
