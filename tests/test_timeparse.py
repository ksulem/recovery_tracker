from datetime import time

import pytest

from recovery.timeparse import in_quiet_hours, parse_time, parse_time_range, parse_timezone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9:30", time(9, 30)),
        ("09.30", time(9, 30)),
        ("21 00", time(21, 0)),
        ("8", time(8, 0)),
        (" 7:05 ", time(7, 5)),
        ("24:00", None),
        ("9:75", None),
        ("утром", None),
    ],
)
def test_parse_time(raw: str, expected: time | None) -> None:
    assert parse_time(raw) == expected


def test_parse_time_range() -> None:
    assert parse_time_range("22:00-08:00") == (time(22), time(8))
    assert parse_time_range("23 – 7") == (time(23), time(7))
    assert parse_time_range("22:00-22:00") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Asia/Tbilisi", "Asia/Tbilisi"),
        ("asia/tbilisi", "Asia/Tbilisi"),
        ("Europe/Tbilisi", None),  # not a real zone
        ("+4", "Etc/GMT-4"),
        ("UTC+04:00", "Etc/GMT-4"),
        ("GMT-3", "Etc/GMT+3"),
        ("+0", "UTC"),
        ("+15", None),
        ("Тбилиси", None),
    ],
)
def test_parse_timezone(raw: str, expected: str | None) -> None:
    assert parse_timezone(raw) == expected


def test_quiet_hours_wrapping_midnight() -> None:
    assert in_quiet_hours(time(23, 30), time(22), time(8))
    assert in_quiet_hours(time(7, 59), time(22), time(8))
    assert not in_quiet_hours(time(8, 0), time(22), time(8))
    assert in_quiet_hours(time(14), time(13), time(15))
    assert not in_quiet_hours(time(14), None, None)
