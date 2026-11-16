"""Evening wind-down timing.

Agreed with the psychologist: the day review happens at least an hour before
sleep, and thoughts that come up later are parked instead of being chewed over
until deep night. From ``bedtime − 1 h`` until ``bedtime + 7 h`` the bot is in
*late mode*: no reminders or offers, free text is parked with a one-line reply,
and ``/evening`` offers a short version. Without a bedtime this is all off.
"""

from datetime import time

from recovery.clock import local_now
from recovery.db.models import User
from recovery.timeparse import in_quiet_hours

BEFORE_BEDTIME_MIN = 60  # late mode starts an hour before sleep
LATE_UNTIL_AFTER_BEDTIME_MIN = 7 * 60
# Suggested evening check-in: 1 h 15 min before the boundary (20:45 for a 23:00
# bedtime), leaving time for the check-in itself and to unwind afterwards.
SUGGEST_BEFORE_BOUNDARY_MIN = 75
# An evening check-in less than 15 min before the boundary is "too late".
TOO_LATE_MARGIN_MIN = 15


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _at(minutes: int) -> time:
    minutes %= 24 * 60
    return time(minutes // 60, minutes % 60)


def boundary(user: User) -> time | None:
    """When late mode starts (bedtime − 1 h)."""
    if user.bedtime is None:
        return None
    return _at(_minutes(user.bedtime) - BEFORE_BEDTIME_MIN)


def late_until(user: User) -> time | None:
    if user.bedtime is None:
        return None
    return _at(_minutes(user.bedtime) + LATE_UNTIL_AFTER_BEDTIME_MIN)


def is_late(user: User, at: time | None = None) -> bool:
    start, end = boundary(user), late_until(user)
    if start is None or end is None:
        return False
    now = at if at is not None else local_now(user.timezone).time()
    return in_quiet_hours(now, start, end)


def is_night(user: User, at: time | None = None) -> bool:
    """Late mode *after midnight*: the evening is over, and a check-in started
    now would land on the next calendar day."""
    now = at if at is not None else local_now(user.timezone).time()
    start = boundary(user)
    return is_late(user, now) and start is not None and now < start


def suggested_evening_time(user: User) -> time | None:
    start = boundary(user)
    if start is None:
        return None
    return _at(_minutes(start) - SUGGEST_BEFORE_BOUNDARY_MIN)


def evening_too_late(user: User) -> bool:
    """The scheduled evening check-in is less than ~an hour before sleep."""
    start, end = boundary(user), late_until(user)
    if start is None or end is None:
        return False
    return in_quiet_hours(user.evening_time, _at(_minutes(start) - TOO_LATE_MARGIN_MIN), end)


def on_time(completed_local: time, bedtime: time) -> bool:
    """Was a day review finished at least an hour before ``bedtime``?

    Times after midnight but before the late window ends count as late.
    """
    start = _at(_minutes(bedtime) - BEFORE_BEDTIME_MIN)
    end = _at(_minutes(bedtime) + LATE_UNTIL_AFTER_BEDTIME_MIN)
    return not in_quiet_hours(completed_local, start, end)
