"""Tag hierarchy and tag policy: which identified objects become tags.

Levels: 1 constellation, 2 Sun/Moon/planet, 3 catalog object (Messier/NGC/IC), 4 asterism, 5 named star.
The engine owns the policy and marks each object ``taggable``; consumers only filter
``taggable and confidence >= min_confidence`` (contract v1 additive fields).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVEL_CONSTELLATION, LEVEL_BODY, LEVEL_CATALOG, LEVEL_ASTERISM, LEVEL_STAR = 1, 2, 3, 4, 5
DEFAULT_LEVELS = (1, 2, 3, 4)
DEFAULT_MAX_CATALOG_MAG = 6.0
TAG_STYLES = ("plain", "kind")

# Asterisms as lists of Hipparcos ids (facts written for this project; not taken from any dataset).
ASTERISMS: dict[str, tuple[int, ...]] = {
    "Orion's Belt": (26727, 26311, 25930),
    "Big Dipper": (54061, 53910, 58001, 59774, 62956, 65378, 67301),
    "Little Dipper": (11767, 85822, 82080, 77055, 79822, 75097, 72607),
    "Summer Triangle": (91262, 102098, 97649),
    "Winter Triangle": (27989, 37279, 32349),
    "Winter Hexagon": (32349, 37279, 37826, 24608, 21421, 24436),
    "Spring Triangle": (69673, 65474, 49669),
    "Great Square of Pegasus": (113963, 113881, 1067, 677),
    "Sickle of Leo": (49669, 49583, 50583, 50335, 48455, 47908),
    "Teapot": (90185, 89931, 90496, 92855, 93506, 88635, 92041, 93864),
    "Northern Cross": (102098, 100453, 102488, 97165, 95947),
    "Keystone": (84380, 81833, 81693, 83207),
    "W of Cassiopeia": (746, 3179, 4427, 6686, 8886),
}
# stick figure drawn on the overlay: consecutive members joined, closed for the polygons
ASTERISM_CLOSED = {"Summer Triangle", "Winter Triangle", "Winter Hexagon", "Spring Triangle",
                   "Great Square of Pegasus", "Keystone", "Teapot"}
ASTERISM_MIN_FRACTION = 0.8  # share of member stars that must be in frame and matched


@dataclass
class TagPolicy:
    levels: tuple[int, ...] = DEFAULT_LEVELS
    star_mag_limit: float | None = None  # set -> level 5 on, only stars at least this bright
    max_catalog_mag: float = DEFAULT_MAX_CATALOG_MAG
    tag_style: str = "plain"
    extra: dict = field(default_factory=dict)

    def level_on(self, level: int) -> bool:
        if level == LEVEL_STAR and self.star_mag_limit is not None:
            return True
        return level in self.levels

    def as_dict(self) -> dict:
        return {"levels": list(self.levels), "star_mag_limit": self.star_mag_limit,
                "max_catalog_mag": self.max_catalog_mag, "tag_style": self.tag_style}


def parse_levels(v) -> tuple[int, ...]:
    if v is None:
        return DEFAULT_LEVELS
    if isinstance(v, str):
        v = [p for p in re.split(r"[,\s]+", v.strip()) if p]
    out = tuple(sorted({int(x) for x in v}))
    if any(x not in (1, 2, 3, 4, 5) for x in out):
        raise ValueError(f"tag levels must be 1..5, got {v!r}")
    return out


def make_policy(cfg: dict | None = None, levels=None, star_mag_limit=None, max_catalog_mag=None,
                tag_style=None) -> TagPolicy:
    """Flags override astro-id.json ``tag_policy`` which overrides the defaults."""
    cfg = dict(cfg or {})
    lv = parse_levels(levels if levels is not None else cfg.get("levels"))
    sml = star_mag_limit if star_mag_limit is not None else cfg.get("star_mag_limit")
    mcm = max_catalog_mag if max_catalog_mag is not None else cfg.get("max_catalog_mag")
    style = (tag_style or cfg.get("tag_style") or "plain").lower()
    if style not in TAG_STYLES:
        raise ValueError(f"tag style must be one of {TAG_STYLES}, got {style!r}")
    return TagPolicy(lv, None if sml is None else float(sml),
                     DEFAULT_MAX_CATALOG_MAG if mcm is None else float(mcm), style)


def make_tag(name: str) -> str:
    t = name.lower().replace(",", " ")
    t = " ".join(t.split())
    return t[:64].rstrip()


def kind_prefix(level: int, name: str, kind: str) -> str:
    if level == LEVEL_CONSTELLATION:
        return "constellation"
    if level == LEVEL_BODY:
        return {"moon": "moon"}.get(kind, "sun" if name == "Sun" else "planet")
    if level == LEVEL_CATALOG:
        if re.match(r"M\d", name):
            return "messier"
        if name.startswith("NGC "):
            return "ngc"
        if name.startswith("IC "):
            return "ic"
        return "catalog"
    if level == LEVEL_ASTERISM:
        return "asterism"
    return "star"


def styled_tag(name: str, level: int, kind: str, style: str = "plain") -> str:
    base = make_tag(name)
    if style != "kind":
        return base
    prefix = kind_prefix(level, name, kind)
    if base == prefix:  # "moon", "sun"
        return base
    return f"{prefix}/{base}"[:64].rstrip()


def constellation_tag(abbr_or_name: str, style: str = "plain") -> str:
    from .data import CONSTELLATION_NAMES

    name = CONSTELLATION_NAMES.get(abbr_or_name, abbr_or_name)
    return styled_tag(name, LEVEL_CONSTELLATION, "constellation", style)


def constellation_of(ra, dec) -> list[str | None]:
    """IAU constellation abbreviation (Roman 1987 boundaries, bundled with astropy) per position."""
    import numpy as np
    from astropy.coordinates import SkyCoord

    ra, dec = np.atleast_1d(ra), np.atleast_1d(dec)
    out: list[str | None] = [None] * len(ra)
    ok = np.isfinite(ra) & np.isfinite(dec)
    if ok.any():
        names = SkyCoord(ra[ok], dec[ok], unit="deg").get_constellation(short_name=True)
        for i, n in zip(np.nonzero(ok)[0], np.atleast_1d(names)):
            out[int(i)] = str(n)
    return out


def apply_policy(objs, policy: TagPolicy, min_confidence: int) -> None:
    """Set ``taggable`` on each object (in place).

    Members (``member_of`` set) are suppressed when their group is itself tagged
    (taggable and at least ``min_confidence``)."""
    by_tag = {}
    for o in objs:
        o.taggable = _level_ok(o, policy)
        by_tag.setdefault(o.tag, o)
    for o in objs:
        groups = list(o.groups or ([o.member_of] if o.member_of else []))
        present = [g for g in groups if g in by_tag and by_tag[g].taggable
                   and by_tag[g].confidence >= min_confidence]
        if present:
            o.member_of = present[0]
            o.taggable = False
        elif groups:
            o.member_of = groups[0]


def _level_ok(o, policy: TagPolicy) -> bool:
    if not policy.level_on(o.level):
        return False
    if o.level == LEVEL_STAR and policy.star_mag_limit is not None:
        return o.mag is not None and o.mag <= policy.star_mag_limit
    if o.level == LEVEL_CATALOG:
        return o.mag is not None and o.mag <= policy.max_catalog_mag
    return True
