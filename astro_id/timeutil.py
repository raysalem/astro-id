"""Clock-offset and tolerance parsing, true-time computation."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_OFFSET_RE = re.compile(
    r"^\s*(?P<sign>[+-])?\s*(?:(?P<d>\d+(?:\.\d+)?)\s*d)?\s*(?:(?P<h>\d+(?:\.\d+)?)\s*h)?\s*"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*m(?:in)?)?\s*(?:(?P<s>\d+(?:\.\d+)?)\s*s)?\s*$",
    re.IGNORECASE,
)
_HMS_RE = re.compile(r"^\s*(?P<sign>[+-])?\s*(?:(?P<d>\d+)\s*d\s*)?(?P<h>\d+):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*$")


def parse_offset(text: str | None) -> timedelta:
    """'+2559d11h08m', '-1h30m', '90m', '2559d 11:08', '-00:30' -> timedelta. Empty -> 0."""
    if text is None or str(text).strip() == "":
        return timedelta(0)
    s = str(text).strip()
    m = _HMS_RE.match(s)
    if m:
        td = timedelta(days=int(m["d"] or 0), hours=int(m["h"]), minutes=int(m["m"]), seconds=int(m["s"] or 0))
        return -td if m["sign"] == "-" else td
    m = _OFFSET_RE.match(s)
    if not m or not any(m[k] for k in ("d", "h", "m", "s")):
        raise ValueError(f"cannot parse clock offset {text!r} (expected e.g. +2559d11h08m)")
    td = timedelta(days=float(m["d"] or 0), hours=float(m["h"] or 0), minutes=float(m["m"] or 0),
                   seconds=float(m["s"] or 0))
    return -td if m["sign"] == "-" else td


def format_offset(td: timedelta) -> str:
    sign = "-" if td < timedelta(0) else "+"
    total = abs(int(td.total_seconds()))
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    out = sign + (f"{d}d" if d else "") + (f"{h}h" if h or d else "") + f"{m:02d}m"
    return out + (f"{s:02d}s" if s else "")


def parse_tolerance(text) -> float:
    """'15m', '15', '1h', '90s', 15 -> minutes (float)."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|min|h|s)?$", s)
    if not m:
        raise ValueError(f"cannot parse time tolerance {text!r} (expected e.g. 15m)")
    v = float(m.group(1))
    unit = m.group(2) or "m"
    return v * 60 if unit == "h" else v / 60 if unit == "s" else v


def get_tz(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def true_time(exif_dt: datetime, offset: timedelta, tz_name: str | None) -> datetime:
    """Camera-local EXIF time + offset, made timezone-aware.

    With no timezone name the system's local zone is used for that date (DST-correct)."""
    naive = exif_dt + offset
    tz = get_tz(tz_name)
    if tz is not None:
        return naive.replace(tzinfo=tz)
    return naive.astimezone()  # interpret as system local time


def window(t: datetime, tol_min: float, steps: int = 5) -> list[datetime]:
    """Evenly spaced samples over [t - tol, t + tol] (always includes the ends and t)."""
    if tol_min <= 0:
        return [t]
    n = max(3, steps | 1)
    return [t + timedelta(minutes=tol_min * (2 * i / (n - 1) - 1)) for i in range(n)]


def iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return t.replace(microsecond=0).isoformat()


def to_utc(t: datetime) -> datetime:
    return t.astimezone(timezone.utc)
