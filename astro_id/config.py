"""Per-folder astro-id.json lookup and flag merging."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .tagpolicy import TagPolicy, make_policy
from .timeutil import get_tz, parse_offset, parse_tolerance

log = logging.getLogger("astro_id")
CONFIG_NAME = "astro-id.json"
DEFAULT_TOLERANCE_MIN = 15.0
STACK_MODES = ("representative", "all")
DEFAULT_STACK_GAP_S = 120.0
DEFAULT_STACK_TOL = 0.02  # max pointing change, as a fraction of the frame diagonal


@dataclass
class Site:
    lat: float
    lon: float
    elev_m: float = 0.0
    name: str | None = None


@dataclass
class FrameConfig:
    offset: timedelta
    tolerance_min: float
    timezone: str | None
    site: Site | None
    time_source: str  # "config" | "exif" | "facet"
    config_path: Path | None
    time_override: datetime | None = None  # --time: true local time of this frame (Facet's date)
    policy: TagPolicy | None = None


@dataclass
class StackConfig:
    mode: str = "representative"
    gap_s: float = DEFAULT_STACK_GAP_S
    tol: float = DEFAULT_STACK_TOL


def stack_config(folder: Path, mode_flag: str | None = None, gap_flag: float | None = None) -> StackConfig:
    """Flags override astro-id.json ``stack_mode`` / ``stack_gap_s`` / ``stack_tolerance``."""
    cfg = load_config(find_config(folder))
    mode = (mode_flag or cfg.get("stack_mode") or "representative").lower()
    if mode not in STACK_MODES:
        raise ValueError(f"stack mode must be one of {STACK_MODES}, got {mode!r}")
    gap = gap_flag if gap_flag is not None else cfg.get("stack_gap_s", DEFAULT_STACK_GAP_S)
    return StackConfig(mode, float(gap), float(cfg.get("stack_tolerance", DEFAULT_STACK_TOL)))


def find_config(folder: Path) -> Path | None:
    """Nearest astro-id.json walking up from `folder` (inclusive)."""
    folder = folder.resolve() if not str(folder).startswith("\\\\") else folder
    for p in [folder, *folder.parents]:
        c = p / CONFIG_NAME
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def load_config(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def parse_site(text: str) -> Site:
    """'lat,lon[,elev_m]' -> Site."""
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) not in (2, 3):
        raise ValueError(f"--site expects lat,lon[,elev_m], got {text!r}")
    lat, lon = float(parts[0]), float(parts[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"site out of range: {text!r}")
    return Site(lat, lon, float(parts[2]) if len(parts) == 3 else 0.0)


def site_from_dict(d: dict | None) -> Site | None:
    if not d or "lat" not in d or "lon" not in d:
        return None
    return Site(float(d["lat"]), float(d["lon"]), float(d.get("elev_m") or 0.0), d.get("name"))


_ELEV_CACHE: dict[tuple[float, float], float] = {}


def site_with_elevation(site: Site) -> Site:
    """Elevation from USGS EPQS (cached per rounded coordinate); 0 m on failure."""
    from . import site as site_mod

    key = (round(site.lat, 3), round(site.lon, 3))
    if key not in _ELEV_CACHE:
        elev = site_mod.fetch_elevation(site.lat, site.lon)
        if elev is None:
            log.warning("elevation lookup failed for %.4f,%.4f; using 0 m", site.lat, site.lon)
            return Site(site.lat, site.lon, 0.0, site.name)  # failures are not cached
        _ELEV_CACHE[key] = elev
    return Site(site.lat, site.lon, _ELEV_CACHE[key], site.name)


_TZ_CACHE: dict[tuple[float, float], str | None] = {}


def infer_timezone(lat: float, lon: float) -> str | None:
    """IANA zone name for a coordinate (timezonefinder, offline); None if unavailable."""
    key = (round(lat, 2), round(lon, 2))
    if key not in _TZ_CACHE:
        try:
            from timezonefinder import TimezoneFinder

            _TZ_CACHE[key] = TimezoneFinder().timezone_at(lat=lat, lng=lon)
        except Exception as exc:
            log.warning("could not infer timezone for %.4f,%.4f: %s", lat, lon, exc)
            _TZ_CACHE[key] = None
    return _TZ_CACHE[key]


def parse_time_flag(text: str, tz_name: str | None) -> datetime:
    """ISO-8601 with offset, or naive + timezone name (else system local) -> aware datetime."""
    try:
        t = datetime.fromisoformat(str(text).strip().replace(" ", "T", 1).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"--time expects ISO-8601 like 2026-11-07T20:34:00-08:00, got {text!r}") from None
    if t.tzinfo is None:
        if tz_name and get_tz(tz_name) is None:
            raise ValueError(f"unknown timezone {tz_name!r}")
        t = t.replace(tzinfo=get_tz(tz_name)) if tz_name else t.astimezone()
    return t


def resolve(folder: Path, offset_flag: str | None, tol_flag: str | None, site_flag: str | None,
            time_flag: str | None = None, tz_flag: str | None = None, policy_flags: dict | None = None) -> FrameConfig:
    """Flags override the nearest astro-id.json."""
    cpath = find_config(folder)
    cfg = load_config(cpath)
    policy = make_policy(cfg.get("tag_policy"), **(policy_flags or {}))
    offset_txt = offset_flag if offset_flag is not None else cfg.get("clock_offset")
    have_offset = offset_txt is not None and str(offset_txt).strip() != ""
    tol = parse_tolerance(tol_flag) if tol_flag is not None else float(cfg.get("time_tolerance_min", DEFAULT_TOLERANCE_MIN))
    site = parse_site(site_flag) if site_flag else site_from_dict(cfg.get("site"))
    if site_flag and len([p for p in site_flag.split(",") if p.strip()]) == 2:
        site = site_with_elevation(site)
    # timezone: astro-id.json, else inferred from the site, else None (= system local zone)
    tz_name = cfg.get("timezone") or (infer_timezone(site.lat, site.lon) if site else None)
    t_over = parse_time_flag(time_flag, tz_flag or tz_name) if time_flag else None
    return FrameConfig(
        offset=parse_offset(offset_txt) if have_offset else timedelta(0),
        tolerance_min=tol,
        timezone=tz_name,
        site=site,
        # a config file that states a clock_offset (even blank = camera clock is right) counts as config
        time_source="facet" if t_over else (
            "config" if (offset_flag is not None or "clock_offset" in cfg) else "exif"),
        config_path=cpath,
        time_override=t_over,
        policy=policy,
    )
