"""Per-folder astro-id.json lookup and flag merging."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .timeutil import parse_offset, parse_tolerance

CONFIG_NAME = "astro-id.json"
DEFAULT_TOLERANCE_MIN = 15.0


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
    time_source: str  # "config" | "exif"
    config_path: Path | None


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


def resolve(folder: Path, offset_flag: str | None, tol_flag: str | None, site_flag: str | None) -> FrameConfig:
    """Flags override the nearest astro-id.json."""
    cpath = find_config(folder)
    cfg = load_config(cpath)
    offset_txt = offset_flag if offset_flag is not None else cfg.get("clock_offset")
    have_offset = offset_txt is not None and str(offset_txt).strip() != ""
    tol = parse_tolerance(tol_flag) if tol_flag is not None else float(cfg.get("time_tolerance_min", DEFAULT_TOLERANCE_MIN))
    site = parse_site(site_flag) if site_flag else site_from_dict(cfg.get("site"))
    return FrameConfig(
        offset=parse_offset(offset_txt) if have_offset else timedelta(0),
        tolerance_min=tol,
        timezone=cfg.get("timezone"),
        site=site,
        # a config file that states a clock_offset (even blank = camera clock is right) counts as config
        time_source="config" if (offset_flag is not None or "clock_offset" in cfg) else "exif",
        config_path=cpath,
    )
