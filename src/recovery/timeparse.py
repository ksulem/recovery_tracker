"""Parsing of user-typed times and timezones."""

import re
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

_TIME = re.compile(r"^\s*(\d{1,2})\s*[:.\- ]\s*(\d{2})\s*$|^\s*(\d{1,2})\s*$")
_OFFSET = re.compile(r"^\s*(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(00))?\s*$", re.IGNORECASE)
_RANGE = re.compile(r"^\s*(.+?)\s*[-–—]\s*(.+?)\s*$")


def parse_time(raw: str) -> time | None:
    """'9:30', '09.30', '21 00', '8' → time; None if not a valid time."""
    m = _TIME.match(raw)
    if not m:
        return None
    hour = int(m.group(1) or m.group(3))
    minute = int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_time_range(raw: str) -> tuple[time, time] | None:
    """'22:00-08:00' → (22:00, 08:00)."""
    m = _RANGE.match(raw)
    if not m:
        return None
    start, end = parse_time(m.group(1)), parse_time(m.group(2))
    if start is None or end is None or start == end:
        return None
    return start, end


def _canonical_zones() -> dict[str, str]:
    return {name.lower(): name for name in available_timezones()}


def parse_timezone(raw: str) -> str | None:
    """IANA name (any case) or a whole-hour UTC offset ('+4', 'UTC+04:00').

    Offsets map to ``Etc/GMT∓N`` — note the inverted sign of the Etc zones.
    A city name is preferable (it follows DST), so it is tried first.
    """
    text = raw.strip().replace(" ", "_")
    if not text:
        return None
    name = _canonical_zones().get(text.lower())
    if name:
        return name
    m = _OFFSET.match(raw)
    if m:
        sign, hours = m.group(1), int(m.group(2))
        if hours == 0:
            return "UTC"
        if hours > 14:
            return None
        zone = f"Etc/GMT{'-' if sign == '+' else '+'}{hours}"
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError:
            return None
        return zone
    return None


def in_quiet_hours(now: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end  # wraps midnight
