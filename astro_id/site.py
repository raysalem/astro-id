"""`astro-id site <folder>`: pick the observing site on Google Maps and save it to astro-id.json.

Parsing and merging are pure functions so they can be tested without a browser or a prompt.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from .config import CONFIG_NAME, Site, infer_timezone, load_config, site_from_dict
from .timeutil import parse_offset

DEFAULT_SITE = Site(32.6064, -116.3239, 1100.0, "Tierra del Sol")
EPQS = "https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&units=Meters&wkid=4326"

_NUM = r"[-+]?\d{1,3}(?:\.\d+)?"
_DMS = re.compile(
    r"(\d{1,3})\s*[°d]\s*(\d{1,2})?\s*['′m]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"″s]?\s*([NSEW])", re.IGNORECASE)


def _check(lat: float, lon: float) -> tuple[float, float]:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordinates out of range: {lat}, {lon}")
    return lat, lon


def parse_maps_url(text: str) -> tuple[float, float] | None:
    """Google Maps URL -> (lat, lon). Handles '@lat,lon,zoom', '!3dLAT!4dLON', 'q=lat,lon', 'll=lat,lon'."""
    t = unquote(text.strip())
    m = re.search(r"!3d(" + _NUM + r")!4d(" + _NUM + ")", t)  # the dropped pin beats the view center
    if m:
        return _check(float(m.group(1)), float(m.group(2)))
    m = re.search(r"@(" + _NUM + r"),\s*(" + _NUM + ")", t)
    if m:
        return _check(float(m.group(1)), float(m.group(2)))
    m = re.search(r"[?&](?:q|ll|query|center|destination)=(" + _NUM + r"),\s*(" + _NUM + ")", t)
    if m:
        return _check(float(m.group(1)), float(m.group(2)))
    return None


def parse_latlon(text: str) -> tuple[float, float]:
    """Pasted text -> (lat, lon). Accepts '32.6064, -116.3239' (Maps right-click copy), a Maps URL,
    or degrees-minutes-seconds like 32°36'23"N 116°19'26"W."""
    t = text.strip().strip("()[]")
    if not t:
        raise ValueError("empty input")
    if "google." in t or "goo.gl" in t or t.startswith("http") or "@" in t:
        r = parse_maps_url(t)
        if r:
            return r
        raise ValueError("no coordinates in that URL (use the full URL with @lat,lon, or right-click the pin)")
    dms = _DMS.findall(t)
    if len(dms) == 2:
        vals = []
        for d, m, s, h in dms:
            v = float(d) + float(m or 0) / 60 + float(s or 0) / 3600
            vals.append((-v if h.upper() in "SW" else v, h.upper()))
        lat = next(v for v, h in vals if h in "NS")
        lon = next(v for v, h in vals if h in "EW")
        return _check(lat, lon)
    nums = re.findall(_NUM, t)
    if len(nums) >= 2:
        return _check(float(nums[0]), float(nums[1]))
    raise ValueError(f"cannot read coordinates from {text!r}")


_ELEV = r"[-+]?\d{1,5}(?:\.\d+)?"
_EARTH_AT = re.compile(r"@(" + _NUM + r"),\s*(" + _NUM + r"),\s*(" + _ELEV + r")a(?![0-9A-Za-z])")
EARTH_URL = "https://earth.google.com/web/"
MAPS_URL = "https://www.google.com/maps/"


def parse_location(text: str) -> tuple[float, float, float | None]:
    """Pasted text -> (lat, lon, elevation in metres or None), for the add-on's location step (R-LOC).

    Accepts a Google Earth URL (``.../@lat,lon,ALTa,...``: the ``a`` value is the ground altitude of the point),
    a Google Maps URL (``@lat,lon,...``, ``!3d..!4d..``; no elevation), decimal ``lat, lon[, elev]`` (an ``m`` after
    the elevation is fine), or degrees-minutes-seconds like ``32°36'23"N 116°19'26"W`` with an optional elevation.
    """
    t = text.strip().strip("()[]")
    if not t:
        raise ValueError("empty input")
    if "earth.google." in t or _EARTH_AT.search(unquote(t)):
        m = _EARTH_AT.search(unquote(t))
        if m:
            lat, lon = _check(float(m.group(1)), float(m.group(2)))
            return lat, lon, float(m.group(3))
        r = parse_maps_url(t)
        if r:
            return r[0], r[1], None
        raise ValueError("no coordinates in that Google Earth URL (copy it after the view settles on the spot)")
    if "google." in t or "goo.gl" in t or t.startswith("http") or "@" in t:
        r = parse_maps_url(t)
        if r:
            return r[0], r[1], None
        raise ValueError("no coordinates in that URL (use the full URL with @lat,lon, or right-click the pin)")
    dms = _DMS.findall(t)
    if len(dms) == 2:
        lat, lon = parse_latlon(t)
        rest = _DMS.sub(" ", t)
        m = re.search(r"(?<![\d.])(" + _ELEV + r")\s*m?(?![0-9A-Za-z])", rest)
        return lat, lon, (float(m.group(1)) if m else None)
    if dms:
        raise ValueError(f"cannot read coordinates from {text!r}")
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", t)
    if len(nums) in (2, 3):
        lat, lon = _check(float(nums[0]), float(nums[1]))
        return lat, lon, (float(nums[2]) if len(nums) == 3 else None)
    raise ValueError(f"cannot read coordinates from {text!r}")


def maps_url(site: Site) -> str:
    return f"https://www.google.com/maps/@{site.lat:.5f},{site.lon:.5f},15z/data=!3m1!1e3"


def parse_epqs(payload: dict) -> float | None:
    v = payload.get("value")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v < -1000 else v  # EPQS returns -1000000 outside coverage


def fetch_elevation(lat: float, lon: float, timeout: int = 20) -> float | None:
    import requests

    try:
        r = requests.get(EPQS.format(lat=lat, lon=lon), timeout=timeout)
        r.raise_for_status()
        return parse_epqs(r.json())
    except Exception:
        return None


def merge_config(existing: dict, site: Site, offset: str | None = None, timezone: str | None = None) -> dict:
    """New site into an existing astro-id.json dict, keeping clock_offset / tolerance / timezone."""
    out = dict(existing)
    out["site"] = {"name": site.name, "lat": round(site.lat, 6), "lon": round(site.lon, 6),
                   "elev_m": None if site.elev_m is None else round(float(site.elev_m), 1)}
    if "clock_offset" not in out:
        out["clock_offset"] = offset or ""
    out.setdefault("time_tolerance_min", 15)
    if not out.get("timezone"):  # never null: inferred from the site, else omitted (= system local zone)
        out.pop("timezone", None)
        tz = timezone or infer_timezone(site.lat, site.lon)
        if tz:
            out["timezone"] = tz
    order = ["clock_offset", "time_tolerance_min", "timezone", "site"]
    return {k: out[k] for k in order if k in out} | {k: v for k, v in out.items() if k not in order}


def open_maps(site: Site) -> None:
    url = maps_url(site)
    for chrome in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", shutil.which("google-chrome"),
                   shutil.which("chrome")]:
        if chrome and Path(chrome).exists():
            subprocess.Popen([chrome, url])
            return
    webbrowser.open(url)


def run_site(folder: Path, ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
             open_browser: Callable[[Site], None] = open_maps,
             elevation: Callable[[float, float], float | None] = fetch_elevation) -> int:
    folder = Path(folder)
    if not folder.is_dir():
        say(f"not a folder: {folder}")
        return 2
    path = folder / CONFIG_NAME
    existing = load_config(path) if path.exists() else {}
    current = site_from_dict(existing.get("site")) or DEFAULT_SITE
    say(f"Current site: {current.name or '(unnamed)'} {current.lat:.5f}, {current.lon:.5f}")
    open_browser(current)
    say("In Google Maps, right-click the exact spot and click the first line (lat, long) to copy it.")
    while True:
        txt = ask("Paste lat, lon (or a Maps URL; blank keeps the current site): ").strip()
        if not txt:
            lat, lon = current.lat, current.lon
            break
        try:
            lat, lon = parse_latlon(txt)
            break
        except ValueError as exc:
            say(f"  {exc}")
    elev = elevation(lat, lon)
    if elev is None:
        while True:
            e = ask("Elevation lookup failed. Elevation in meters (blank = 0): ").strip()
            try:
                elev = float(e) if e else 0.0
                break
            except ValueError:
                say("  enter a number")
    else:
        say(f"Elevation (USGS): {elev:.0f} m")
    name = ask(f"Site name [{current.name or ''}]: ").strip() or current.name or "site"
    offset = None
    if "clock_offset" not in existing:
        while True:
            o = ask("Camera clock offset to add to EXIF time, e.g. +2559d11h08m (blank = camera clock is right): ").strip()
            try:
                parse_offset(o)
                offset = o
                break
            except ValueError as exc:
                say(f"  {exc}")
    site = Site(lat, lon, elev, name)
    say(f"\n  name {site.name}\n  lat  {lat:.6f}\n  lon  {lon:.6f}\n  elev {elev:.0f} m\n  file {path}")
    if ask("Save? [y/N] ").strip().lower() not in ("y", "yes"):
        say("Not saved.")
        return 1
    path.write_text(json.dumps(merge_config(existing, site, offset), indent=2) + "\n", encoding="utf-8")
    say(f"Saved {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_site(Path(sys.argv[1])))
