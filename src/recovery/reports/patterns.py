"""Evening → next morning, day episodes and the week-2 hypotheses.

Facts side by side and counts — never averages, never "X caused Y".
Fewer than ``MIN_CASES`` relevant cases → "данных недостаточно" (spec §7: a
pattern needs at least three occurrences before it is even worth naming).
The psychologist and the owner interpret; the bot only lines things up.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from recovery.db.models import Checkin, DayEpisode
from recovery.domain import CheckinType, MarkKind
from recovery.reports.digest import (
    Day,
    Digest,
    _label,
    _labels,
    _ordered_episodes,
    episode_name,
    onset_text,
    pressure_label,
)
from recovery.texts import ru

MIN_CASES = 3

EFFECT_SHORT = {"gave": "дало", "took": "забрало", "both": "оба", "neutral": "нейтрально"}


def _effect_short(e: DayEpisode) -> str:
    return EFFECT_SHORT.get(e.effect or "", "без оценки")


def short_date(d: date) -> str:
    return f"{ru.WEEKDAYS[d.weekday()]} {d.day}"


def _enough(n: int) -> str:
    return "" if n >= MIN_CASES else f" {ru.NOT_ENOUGH_DATA}"


def _charges(checkins: Iterable[Checkin | None]) -> str:
    values = [f"{c.battery_pct}%" for c in checkins if c is not None and c.battery_pct is not None]
    return ", ".join(values) if values else "—"


@dataclass
class Pair:
    evening: Checkin
    day: Day
    morning: Checkin | None
    next_day: Day | None


def pairs(digest: Digest) -> list[Pair]:
    by_date = {d.date: d for d in digest.days}
    out = []
    for day in digest.days:
        next_day = by_date.get(day.date + timedelta(days=1))
        mornings = next_day.of_type(CheckinType.MORNING) if next_day else []
        for evening in day.of_type(CheckinType.EVENING):
            out.append(Pair(evening, day, mornings[0] if mornings else None, next_day))
    return out


def _marks(day: Day, c: Checkin, kind: str) -> list[str]:
    return day.marks.get((c.id, kind), [])


def _pressure(day: Day, c: Checkin) -> list[str] | None:
    """Signals; [] if answered "ничего из этого"; None if not asked/skipped."""
    marks = _marks(day, c, MarkKind.PRESSURE)
    if marks:
        return marks
    return [] if (c.answers or {}).get("press") == ["none"] else None


# --- evening → next morning ----------------------------------------------------------
def _evening_parts(p: Pair) -> list[str]:
    c = p.evening
    parts: list[str] = []
    if c.comparison_to_morning:
        comparison = ru.COMPARISON_LABELS[c.comparison_to_morning].lower()
        parts.append(f"по сравнению с утром: {comparison}")
    if c.battery_pct is not None:
        parts.append(f"заряд {c.battery_pct}%")
    if c.obligations:
        parts.append(f"обязательств: {ru.OBLIGATIONS_LABELS[c.obligations].lower()}")
    episodes = _ordered_episodes(c, p.day)
    if episodes:
        parts.append(
            "эпизоды: " + "; ".join(f"{episode_name(e)} — {_effect_short(e)}" for e in episodes)
        )
    if c.preoccupation:
        parts.append(f"мысли занимает: {ru.PREOCCUPATION_LABELS[c.preoccupation].lower()}")
    pressure = _pressure(p.day, c)
    if pressure is not None:
        parts.append(
            "давление: " + (", ".join(pressure_label(v) for v in pressure) if pressure else "нет")
        )
    return parts


def _morning_parts(p: Pair) -> list[str]:
    m = p.morning
    parts: list[str] = []
    if m is None or p.next_day is None:
        return parts
    if m.sleep_quality:
        parts.append(f"после сна: {ru.SLEEP_LABELS[m.sleep_quality].lower()}")
    onset = onset_text(m)
    if onset:
        parts.append(f"заснуть: {onset}")
    pre_sleep = _marks(p.next_day, m, MarkKind.PRE_SLEEP)
    if pre_sleep:
        parts.append(f"перед сном: {_labels(pre_sleep, ru.PRE_SLEEP_LABELS, m.pre_sleep_note)}")
    if m.battery_pct is not None:
        parts.append(f"заряд {m.battery_pct}%")
    if m.physical_score is not None:
        parts.append(f"физическая энергия {m.physical_score}/10")
    return parts


def lower_by_morning(pair_list: list[Pair]) -> tuple[int, int]:
    """(pairs where the morning charge was lower than the evening one, pairs with both)."""
    both = [
        p
        for p in pair_list
        if p.morning is not None
        and p.evening.battery_pct is not None
        and p.morning.battery_pct is not None
    ]
    lower = sum(
        1
        for p in both
        if p.morning is not None
        and p.morning.battery_pct is not None
        and p.evening.battery_pct is not None
        and p.morning.battery_pct < p.evening.battery_pct
    )
    return lower, len(both)


def evening_to_morning_section(digest: Digest) -> str | None:
    pair_list = [p for p in pairs(digest) if p.morning is not None]
    if not pair_list:
        return None
    lines = []
    for p in pair_list:
        assert p.next_day is not None
        evening = ", ".join(_evening_parts(p)) or "нет ответов"
        morning = ", ".join(_morning_parts(p)) or "нет ответов"
        lines.append(
            f"- {short_date(p.day.date)} → {short_date(p.next_day.date)}: "
            f"вечер — {evening}; утро — {morning}"
        )
    lower, total = lower_by_morning(pair_list)
    parts = [ru.EVENING_MORNING_TITLE, "\n".join(lines)]
    if total:
        parts.append(ru.LOWER_BY_MORNING.format(count=lower, total=total) + _enough(total))
    return "\n\n".join(parts)


# --- day episodes ----------------------------------------------------------------------
def _episodes(digest: Digest) -> list[DayEpisode]:
    return [e for d in digest.days for eps in d.episodes.values() for e in eps]


def _effects(episodes: list[DayEpisode]) -> str:
    counts = Counter(e.effect or "unrated" for e in episodes)
    labels = [("gave", "дало"), ("took", "забрало"), ("both", "оба"), ("neutral", "нейтрально")]
    parts = [f"{label} {counts[key]}" for key, label in labels if counts[key]]
    if counts["unrated"]:
        parts.append(f"без оценки {counts['unrated']}")
    return ", ".join(parts)


def _resources(episodes: list[DayEpisode], attr: str) -> str:
    counts = Counter(r for e in episodes for r in getattr(e, attr))
    return ", ".join(f"{_label(r, ru.RESOURCE_LABELS)} {n}" for r, n in counts.most_common())


def episodes_section(digest: Digest) -> str | None:
    episodes = _episodes(digest)
    if not episodes:
        return None
    by_kind: dict[str, list[DayEpisode]] = defaultdict(list)
    for e in episodes:
        by_kind[e.kind].append(e)
    lines = []
    for kind, group in sorted(by_kind.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        name = ru.OTHER_SHORT if kind == "other" else ru.EPISODE_LABELS[kind].lower()
        line = f"- {name} — {len(group)}: {_effects(group)}"
        both = [e for e in group if e.effect == "both"]
        if both:
            gave, took = _resources(both, "gave"), _resources(both, "took")
            details = []
            if gave:
                details.append(f"дало: {gave}")
            if took:
                details.append(f"забрало: {took}")
            if details:
                line += f" (при «оба» — {'; '.join(details)})"
        for choice in ("wanted", "needed"):
            chosen = [e for e in group if e.choice == choice]
            if chosen:
                line += f"; {ru.CHOICE_LABELS[choice].lower()}: {_effects(chosen)}"
        lines.append(line + _enough(len(group)))
    return "\n\n".join([ru.EPISODES_SECTION_TITLE, "\n".join(lines)])


# --- hypotheses ----------------------------------------------------------------------
def _group_line(label: str, group: list[Pair]) -> str:
    mornings = _charges(p.morning for p in group)
    return f"- {label}: {len(group)} веч., утром заряд: {mornings}{_enough(len(group))}"


def hypotheses_section(digest: Digest) -> str | None:
    pair_list = pairs(digest)
    episodes = _episodes(digest)
    blocks: list[str] = []

    # 1. Emotional background vs resource.
    answered = [p for p in pair_list if p.evening.preoccupation]
    if answered:
        busy = [p for p in answered if p.evening.preoccupation in ("some", "strong")]
        calm = [p for p in answered if p.evening.preoccupation == "none"]
        blocks.append(
            "\n".join(
                [
                    ru.H_EMOTIONS,
                    _group_line("что-то занимало мысли", busy),
                    _group_line("ничего не занимало", calm),
                ]
            )
        )

    # 2. Obligations vs recovery.
    with_obl = [p for p in pair_list if p.evening.obligations]
    if with_obl:
        rows = [
            _group_line(
                ru.OBLIGATIONS_LABELS[level].lower(),
                [p for p in with_obl if p.evening.obligations == level],
            )
            for level in ("none", "some", "many")
            if any(p.evening.obligations == level for p in with_obl)
        ]
        blocks.append("\n".join([ru.H_OBLIGATIONS, *rows]))

    # 3. Self-chosen vs necessary activity.
    chosen = [e for e in episodes if e.choice]
    if chosen:
        rows = []
        for choice in ("wanted", "needed"):
            group = [e for e in chosen if e.choice == choice]
            if group:
                rows.append(
                    f"- {ru.CHOICE_LABELS[choice].lower()}: {len(group)} — {_effects(group)}"
                    + _enough(len(group))
                )
        blocks.append("\n".join([ru.H_CHOICE, *rows]))

    # 4. Pressure on oneself vs rest.
    pressured = [(p, _pressure(p.day, p.evening)) for p in pair_list]
    asked = [(p, signals) for p, signals in pressured if signals is not None]
    demands = [e for e in episodes if e.kind == "self_demands"]
    if asked or demands:
        rows = []
        if asked:
            signal_counts = Counter(s for _, signals in asked for s in signals or [])
            which = ", ".join(f"{pressure_label(k)} {n}" for k, n in signal_counts.most_common())
            row = (
                f"- вечеров с вопросом: {len(asked)}, из них с давлением: "
                f"{sum(1 for _, s in asked if s)}"
            )
            rows.append(f"{row} ({which})" if which else row)
            rows.append(_group_line("с давлением", [p for p, s in asked if s]))
            rows.append(_group_line("без давления", [p for p, s in asked if not s]))
        if demands:
            rows.append(
                f"- «требования к себе» как эпизод: {len(demands)} — {_effects(demands)}"
                + _enough(len(demands))
            )
        blocks.append("\n".join([ru.H_PRESSURE, *rows]))

    # 5. Does an evening improvement last until morning?
    better = [p for p in pair_list if p.evening.comparison_to_morning == "better"]
    if better:
        lower, total = lower_by_morning(better)
        blocks.append(
            "\n".join(
                [
                    ru.H_EVENING_GAIN,
                    f"- вечеров «лучше, чем утром»: {len(better)}; утром заряд ниже вечернего: "
                    f"{lower} из {total}{_enough(total)}",
                ]
            )
        )

    if not blocks:
        return None
    return "\n\n".join([ru.HYPOTHESES_SECTION_TITLE, ru.HYPOTHESES_DISCLAIMER, *blocks])
