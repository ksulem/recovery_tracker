"""Factual day-by-day digest for /week and the psychologist session brief.

Rules (spec §7, §13): reflect exactly what was recorded; every dimension is
shown separately, never averaged; skipped answers are simply absent; missing
days are stated as such; no interpretation or conclusions.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery import winddown
from recovery.battery import in_reserve
from recovery.clock import local_today
from recovery.db.models import (
    Checkin,
    CheckinMark,
    CheckinSymptom,
    DayEpisode,
    DiscussionItem,
    LoadEvent,
    Message,
    ParkedThought,
    RecoveryEvent,
    TherapySession,
    User,
)
from recovery.domain import (
    CheckinStatus,
    CheckinType,
    DiscussionStatus,
    InputType,
    MarkKind,
    ThoughtStatus,
)
from recovery.texts import ru

MONTHS_GENITIVE = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

SCORES = [
    ("physical_score", "физический"),
    ("emotional_score", "эмоциональный"),
    ("social_score", "социальный"),
    ("cognitive_score", "когнитивный"),
    ("overall_score", "общее самочувствие"),
]


def fmt_date(d: date, with_weekday: bool = True) -> str:
    text = f"{d.day} {MONTHS_GENITIVE[d.month - 1]} {d.year}"
    return f"{ru.WEEKDAYS[d.weekday()]}, {text}" if with_weekday else text


def fmt_period(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}"
    return f"{fmt_date(start, False)} – {fmt_date(end, False)}"


@dataclass
class Day:
    date: date
    checkins: list[Checkin] = field(default_factory=list)
    symptoms: dict[uuid.UUID, list[str]] = field(default_factory=lambda: defaultdict(list))
    loads: dict[uuid.UUID | None, list[LoadEvent]] = field(
        default_factory=lambda: defaultdict(list)
    )
    recoveries: dict[uuid.UUID | None, list[RecoveryEvent]] = field(
        default_factory=lambda: defaultdict(list)
    )
    notes: list[Message] = field(default_factory=list)
    episodes: dict[uuid.UUID, list[DayEpisode]] = field(default_factory=lambda: defaultdict(list))
    marks: dict[tuple[uuid.UUID, str], list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def has_records(self) -> bool:
        return bool(self.checkins or self.notes)

    def of_type(self, kind: CheckinType) -> list[Checkin]:
        return [c for c in self.checkins if c.checkin_type == kind]


@dataclass
class Digest:
    start: date
    end: date
    days: list[Day]

    @property
    def total_days(self) -> int:
        return len(self.days)

    @property
    def days_with_records(self) -> int:
        return sum(1 for d in self.days if d.has_records)

    def count(self, kind: CheckinType) -> int:
        return sum(len(d.of_type(kind)) for d in self.days)


async def collect(session: AsyncSession, user: User, start: date, end: date) -> Digest:
    days = {start + timedelta(n): Day(start + timedelta(n)) for n in range((end - start).days + 1)}

    checkins = (
        await session.scalars(
            select(Checkin)
            .where(
                Checkin.user_id == user.id,
                Checkin.local_date.between(start, end),
                Checkin.status != CheckinStatus.ABANDONED,
            )
            .order_by(Checkin.created_at_utc)
        )
    ).all()
    by_id = {c.id: c for c in checkins}
    for c in checkins:
        days[c.local_date].checkins.append(c)

    if by_id:
        for s in (
            await session.scalars(
                select(CheckinSymptom).where(CheckinSymptom.checkin_id.in_(by_id))
            )
        ).all():
            days[by_id[s.checkin_id].local_date].symptoms[s.checkin_id].append(s.symptom)
        for mark in (
            await session.scalars(select(CheckinMark).where(CheckinMark.checkin_id.in_(by_id)))
        ).all():
            day = days[by_id[mark.checkin_id].local_date]
            day.marks[(mark.checkin_id, mark.kind)].append(mark.value)
        for e in (
            await session.scalars(select(DayEpisode).where(DayEpisode.checkin_id.in_(by_id)))
        ).all():
            if e.checkin_id is not None:
                days[by_id[e.checkin_id].local_date].episodes[e.checkin_id].append(e)

    loads = (
        await session.scalars(
            select(LoadEvent)
            .where(LoadEvent.user_id == user.id, LoadEvent.local_date.between(start, end))
            .order_by(LoadEvent.created_at_utc)
        )
    ).all()
    for load in loads:
        days[load.local_date].loads[load.checkin_id].append(load)

    recoveries = (
        await session.scalars(
            select(RecoveryEvent)
            .where(RecoveryEvent.user_id == user.id, RecoveryEvent.local_date.between(start, end))
            .order_by(RecoveryEvent.created_at_utc)
        )
    ).all()
    for rec in recoveries:
        days[rec.local_date].recoveries[rec.checkin_id].append(rec)

    notes = (
        await session.scalars(
            select(Message)
            .where(
                Message.user_id == user.id,
                Message.local_date.between(start, end),
                Message.checkin_id.is_(None),
                Message.input_type == InputType.TEXT,
                Message.text.is_not(None),
            )
            .order_by(Message.created_at_utc)
        )
    ).all()
    for m in notes:
        days[m.local_date].notes.append(m)

    return Digest(start, end, [days[d] for d in sorted(days)])


# --- rendering ---------------------------------------------------------------------
def _labels(values: list[str], labels: dict[str, str]) -> str:
    return ", ".join(labels.get(v, v).lower() for v in values)


def _scores_line(c: Checkin) -> str | None:
    parts = [
        f"{name} {getattr(c, column)}" for column, name in SCORES if getattr(c, column) is not None
    ]
    return " · ".join(parts) if parts else None


def _charge(c: Checkin) -> str:
    text = f"{c.battery_pct}%"
    if in_reserve(c.battery_pct, c.battery_reserve_pct):
        text += f" ({ru.BATTERY_IN_RESERVE_MARK})"
    return text


def _marks_line(c: Checkin, day: Day, kind: str, qid: str, labels: dict[str, str]) -> str | None:
    """Marked values; "ничего из этого" when that was the answer; None if skipped."""
    values = day.marks.get((c.id, kind), [])
    if values:
        return _labels(values, labels)
    if (c.answers or {}).get(qid) == ["none"]:
        return labels["none"].lower()
    return None


def pressure_label(value: str) -> str:
    """Short form for reports: 'другое' instead of the button's long label."""
    if value == "other":
        return ru.PRESSURE_OTHER_SHORT
    return ru.PRESSURE_LABELS[value].lower()


def pressure_text(c: Checkin, values: list[str]) -> str | None:
    """'вина за отдых, другое: «…»'; 'ничего из этого'; None if not asked or skipped."""
    if not values:
        return (
            ru.PRESSURE_LABELS["none"].lower()
            if (c.answers or {}).get("press") == ["none"]
            else None
        )
    parts = [pressure_label(v) for v in values if v != "other"]
    if "other" in values:
        parts.append(
            f"{ru.PRESSURE_OTHER_SHORT}: «{c.pressure_note}»"
            if c.pressure_note
            else ru.PRESSURE_OTHER_SHORT
        )
    return ", ".join(parts)


def episode_text(e: DayEpisode) -> str:
    """'общение с близкими — и то, и другое (дало: эмоционально; забрало: телу; по желанию)'."""
    text = ru.EPISODE_LABELS[e.kind].lower()
    if e.effect:
        text += f" — {ru.EFFECT_LABELS[e.effect].lower()}"
    details = []
    if e.gave:
        details.append(f"дало: {_labels(e.gave, ru.RESOURCE_LABELS)}")
    if e.took:
        details.append(f"забрало: {_labels(e.took, ru.RESOURCE_LABELS)}")
    if e.choice:
        details.append(ru.CHOICE_LABELS[e.choice].lower())
    if details:
        text += f" ({'; '.join(details)})"
    return text


def _ordered_episodes(c: Checkin, day: Day) -> list[DayEpisode]:
    order = (c.answers or {}).get("ep") or []
    episodes = day.episodes.get(c.id, [])
    return sorted(episodes, key=lambda e: order.index(e.kind) if e.kind in order else 99)


def _checkin_lines(c: Checkin, day: Day) -> list[str]:
    lines: list[str] = []
    if c.checkin_type == CheckinType.MORNING:
        head = []
        if c.sleep_quality:
            head.append(f"после сна: {ru.SLEEP_LABELS.get(c.sleep_quality, '?').lower()}")
        if c.sleep_onset:
            head.append(f"заснуть: {ru.SLEEP_ONSET_LABELS[c.sleep_onset].lower()}")
        if c.battery_pct is not None:
            head.append(f"заряд: {_charge(c)}")
        if c.physical_score is not None:
            head.append(f"физическая энергия: {c.physical_score}/10")
        if c.pleasant_desire:
            head.append(f"желание приятного: {ru.DESIRE_LABELS[c.pleasant_desire].lower()}")
        if c.day_outlook:
            head.append(f"день: {ru.OUTLOOK_LABELS[c.day_outlook].lower()}")
        if head:
            lines.append(" · ".join(head))
        pre_sleep = _marks_line(c, day, MarkKind.PRE_SLEEP, "presleep", ru.PRE_SLEEP_LABELS)
        if pre_sleep:
            lines.append(f"Перед сном: {pre_sleep}")
        if c.expected_heavy_load:
            lines.append(f"Ожидалось энергозатратное: «{c.expected_heavy_load}»")
    else:
        if c.comparison_to_morning:
            lines.append(
                f"По сравнению с утром: {ru.COMPARISON_LABELS[c.comparison_to_morning].lower()}"
            )
        if c.battery_pct is not None:
            lines.append(f"Заряд: {_charge(c)}")
        if c.checkin_type == CheckinType.AD_HOC and c.overall_score is not None:
            lines.append(f"Общее состояние: {c.overall_score}/10")
        if c.obligations:
            lines.append(f"Обязательств: {ru.OBLIGATIONS_LABELS[c.obligations].lower()}")
        episodes = _ordered_episodes(c, day)
        if episodes:
            lines.append("Эпизоды дня:")
            lines.extend(f"  - {episode_text(e)}" for e in episodes)
        elif (c.answers or {}).get("ep") == ["none"]:
            lines.append("Эпизоды дня: ничего особенного")
        if c.preoccupation:
            text = f"Занимает мысли: {ru.PREOCCUPATION_LABELS[c.preoccupation].lower()}"
            if c.preoccupation_note:
                text += f" — «{c.preoccupation_note}»"
            lines.append(text)
        loads = day.loads.get(c.id, [])
        if loads:
            lines.append(f"Забирало силы: {_labels([e.category for e in loads], ru.LOAD_LABELS)}")
        recs = day.recoveries.get(c.id, [])
        if recs:
            lines.append(f"Помогало: {_labels([e.activity for e in recs], ru.RECOVERY_LABELS)}")
        if c.pleasant_energy:
            lines.append(f"Силы на приятное: {ru.ENERGY_LABELS[c.pleasant_energy].lower()}")
        pressure = pressure_text(c, day.marks.get((c.id, MarkKind.PRESSURE), []))
        if pressure:
            lines.append(f"Давление на себя: {pressure}")
        scores = None if c.checkin_type == CheckinType.AD_HOC else _scores_line(c)
        if scores:
            lines.append(f"Ресурсы (0–10): {scores}")
        if c.interest_score is not None:
            lines.append(f"Интерес: {c.interest_score}/5")
        symptoms = day.symptoms.get(c.id, [])
        if symptoms:
            lines.append(f"Признаки перегрузки: {_labels(symptoms, ru.SYMPTOM_LABELS)}")
        if c.tomorrow_note:
            lines.append(f"На завтра: «{c.tomorrow_note}»")
    if c.notes:
        lines.append(f"Заметки: «{c.notes}»")
    return lines


def render_day(day: Day) -> str:
    out = [f"### {fmt_date(day.date)}"]
    if not day.has_records:
        out.append("_нет записей_")
        return "\n".join(out)
    titles = {
        CheckinType.MORNING: "Утро",
        CheckinType.EVENING: "Вечер",
        CheckinType.AD_HOC: "Отметка",
    }
    for c in day.checkins:
        title = titles[CheckinType(c.checkin_type)]
        if c.checkin_type == CheckinType.AD_HOC:
            title += f" в {c.local_ts:%H:%M}"
        lines = _checkin_lines(c, day)
        if c.status == CheckinStatus.IN_PROGRESS:
            title += " (не закончен)"
        out += ["", f"**{title}**"]  # blank line: keeps Markdown lists apart
        if lines:
            # Lines already formatted as nested items ("  - …") keep their indent.
            out.extend(line if line.startswith("  - ") else f"- {line}" for line in lines)
        else:
            out.append("- ответов нет")
    if day.notes:
        out += ["", "**Записи**"]
        out.extend(f"- {m.local_ts:%H:%M} «{m.text}»" for m in day.notes)
    return "\n".join(out)


def render_days(digest: Digest) -> str:
    return "\n\n".join(render_day(d) for d in digest.days)


def reserve_count_line(digest: Digest) -> str | None:
    """How many mornings were at or below the reserve — a count, never an average."""
    mornings = [
        c
        for d in digest.days
        for c in d.of_type(CheckinType.MORNING)
        if c.battery_pct is not None and c.battery_reserve_pct is not None
    ]
    if not mornings:
        return None
    count = sum(1 for c in mornings if in_reserve(c.battery_pct, c.battery_reserve_pct))
    return ru.BATTERY_RESERVE_COUNT.format(count=count, total=len(mornings))


def battery_section(digest: Digest) -> str | None:
    """The day's charges side by side, as recorded (the exercise, step 3)."""
    names = {CheckinType.MORNING: "утро", CheckinType.EVENING: "вечер"}
    rows = []
    for day in digest.days:
        marks = [
            f"{names.get(CheckinType(c.checkin_type), f'в {c.local_ts:%H:%M}')} {_charge(c)}"
            for c in day.checkins
            if c.battery_pct is not None
        ]
        if marks:
            rows.append(f"- {fmt_date(day.date)}: " + " · ".join(marks))
    if not rows:
        return None
    count = reserve_count_line(digest)
    return "\n\n".join([ru.BATTERY_SECTION_TITLE, "\n".join(rows), *([count] if count else [])])


def day_review_line(digest: Digest) -> str | None:
    """Evenings whose check-in was finished at least an hour before sleep —
    the goal agreed with the psychologist. A count, not a score."""
    evenings = [
        c
        for d in digest.days
        for c in d.of_type(CheckinType.EVENING)
        if c.bedtime is not None
        and c.completed_at_utc is not None
        and c.status == CheckinStatus.COMPLETED
    ]
    if not evenings:
        return None
    count = sum(
        1
        for c in evenings
        if c.bedtime is not None
        and c.completed_at_utc is not None
        and winddown.on_time(c.completed_at_utc.astimezone(ZoneInfo(c.tz_name)).time(), c.bedtime)
    )
    return ru.DAY_REVIEW_ON_TIME.format(count=count, total=len(evenings))


async def thoughts_line(session: AsyncSession, user: User, start: date, end: date) -> str | None:
    rows = (
        await session.scalars(select(ParkedThought).where(ParkedThought.user_id == user.id))
    ).all()

    def resolved_in_period(t: ParkedThought) -> bool:
        if t.resolved_at_utc is None:
            return False
        day = t.resolved_at_utc.astimezone(ZoneInfo(t.tz_name)).date()
        return start <= day <= end

    parked = sum(1 for t in rows if start <= t.local_date <= end)
    resolved = [t for t in rows if resolved_in_period(t)]
    if not parked and not resolved:
        return None

    def count(status: ThoughtStatus) -> int:
        return sum(1 for t in resolved if t.status == status)

    return ru.THOUGHTS_SUMMARY.format(
        parked=parked,
        done=count(ThoughtStatus.DONE),
        session=count(ThoughtStatus.TO_SESSION),
        dropped=count(ThoughtStatus.DROPPED),
        open=sum(1 for t in rows if t.status == ThoughtStatus.OPEN),
    )


async def wind_down_section(session: AsyncSession, user: User, digest: Digest) -> str | None:
    lines = [
        line
        for line in (
            day_review_line(digest),
            await thoughts_line(session, user, digest.start, digest.end),
        )
        if line
    ]
    if not lines:
        return None
    return "\n\n".join([ru.WIND_DOWN_SECTION_TITLE, *lines])


def pattern_sections(digest: Digest) -> list[str]:
    """Evening → next morning, episodes and hypotheses (see reports.patterns)."""
    # Imported here: patterns builds on the Day/Digest types defined in this module.
    from recovery.reports import patterns

    sections = (
        patterns.evening_to_morning_section(digest),
        patterns.episodes_section(digest),
        patterns.hypotheses_section(digest),
    )
    return [s for s in sections if s]


def coverage_line(digest: Digest) -> str:
    return (
        f"Записи есть за {digest.days_with_records} из {digest.total_days} дн. "
        f"Утренних опросов: {digest.count(CheckinType.MORNING)}, "
        f"вечерних: {digest.count(CheckinType.EVENING)}, "
        f"отметок вне расписания: {digest.count(CheckinType.AD_HOC)}."
    )


# --- documents ---------------------------------------------------------------------
async def week_markdown(session: AsyncSession, user: User) -> tuple[str, str]:
    end = local_today(user.timezone)
    start = end - timedelta(days=6)
    digest = await collect(session, user, start, end)
    battery = battery_section(digest)
    wind_down = await wind_down_section(session, user, digest)
    text = "\n\n".join(
        [
            f"# Неделя: {fmt_period(start, end)}",
            coverage_line(digest),
            "_Только записанные факты, без выводов. Ресурсы показаны по отдельности._",
            *([battery] if battery else []),
            *([wind_down] if wind_down else []),
            *pattern_sections(digest),
            "## По дням",
            render_days(digest),
        ]
    )
    return f"week-{end.isoformat()}.md", text + "\n"


async def last_session(session: AsyncSession, user: User) -> TherapySession | None:
    return await session.scalar(
        select(TherapySession)
        .where(TherapySession.user_id == user.id)
        .order_by(TherapySession.session_date.desc(), TherapySession.created_at_utc.desc())
        .limit(1)
    )


async def open_discussion_items(session: AsyncSession, user: User) -> list[DiscussionItem]:
    return list(
        (
            await session.scalars(
                select(DiscussionItem)
                .where(
                    DiscussionItem.user_id == user.id,
                    DiscussionItem.status == DiscussionStatus.OPEN,
                )
                .order_by(DiscussionItem.created_at_utc)
            )
        ).all()
    )


@dataclass
class SessionBrief:
    start: date
    end: date
    digest: Digest
    items: list[DiscussionItem]
    previous_note: str | None
    filename: str
    markdown: str


async def session_brief(session: AsyncSession, user: User) -> SessionBrief:
    """Period: since the previous session (inclusive), or the last 7 days."""
    end = local_today(user.timezone)
    previous = await last_session(session, user)
    start = previous.session_date if previous else end - timedelta(days=6)
    start = min(start, end)
    digest = await collect(session, user, start, end)
    items = await open_discussion_items(session, user)
    note = previous.post_session_note if previous else None

    parts = [
        f"# Сводка к сессии: {fmt_period(start, end)}",
        "_Фактическая сводка записей: без выводов, интерпретаций и диагнозов._",
        "## Данные за период",
        coverage_line(digest),
        "## Что хочу обсудить",
    ]
    if items:
        parts.append("\n".join(f"- {i.text} _(от {fmt_date(i.local_date, False)})_" for i in items))
    else:
        parts.append("_пунктов нет_")
    if note:
        parts += ["## С прошлой сессии", f"«{note}»"]
    battery = battery_section(digest)
    if battery:
        parts.append(battery)
    wind_down = await wind_down_section(session, user, digest)
    if wind_down:
        parts.append(wind_down)
    parts += pattern_sections(digest)
    parts += ["## По дням", render_days(digest)]
    return SessionBrief(
        start=start,
        end=end,
        digest=digest,
        items=items,
        previous_note=note,
        filename=f"session-brief-{end.isoformat()}.md",
        markdown="\n\n".join(parts) + "\n",
    )
