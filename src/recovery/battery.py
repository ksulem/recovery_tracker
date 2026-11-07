"""The "battery" exercise suggested by the owner's psychologist.

Picture a phone-like battery: 100% charge, part of it set aside as a reserve
(in a favourite colour). During the day notice what drains it and what
recharges it. On a low morning, don't blame yourself: plan the day around the
charge you have.

The charge is always the owner's own estimate, given in steps of 10%. The bot
never computes a charge or assigns a "cost" to activities (spec §3.2, §6.4) —
it only shows what was entered and whether it was at or below the reserve.
"""

from recovery.domain import BatteryColor

FEATURE = "battery"

SQUARES = {
    BatteryColor.RED: "🟥",
    BatteryColor.ORANGE: "🟧",
    BatteryColor.YELLOW: "🟨",
    BatteryColor.GREEN: "🟩",
    BatteryColor.BLUE: "🟦",
    BatteryColor.PURPLE: "🟪",
    BatteryColor.BROWN: "🟫",
}
EMPTY = "⬜"
RESERVE_MARK = "|"
RESERVE_OPTIONS = (10, 20, 30, 40, 50)


def bar(pct: int, color: str = BatteryColor.GREEN, reserve: int | None = None) -> str:
    """40% green with a 30% reserve → '🟩🟩🟩|🟩⬜⬜⬜⬜⬜⬜ 40%'."""
    filled = max(0, min(10, round(pct / 10)))
    square = SQUARES.get(BatteryColor(color), SQUARES[BatteryColor.GREEN])
    cells = [square] * filled + [EMPTY] * (10 - filled)
    if reserve is not None and 0 < reserve < 100:
        cells.insert(round(reserve / 10), RESERVE_MARK)
    return f"{''.join(cells)} {pct}%"


def in_reserve(pct: int | None, reserve: int | None) -> bool:
    """At or below the reserve line."""
    return pct is not None and reserve is not None and pct <= reserve
