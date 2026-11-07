"""Factual day-by-day digest for /week and the psychologist session brief.

Rules (spec §7, §13): reflect exactly what was recorded; every dimension is
shown separately, never averaged; skipped answers are simply absent; missing
days are stated as such; no interpretation or conclusions.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.battery import in_reserve
from recovery.clock import local_today
from recovery.db.models import (
    Checkin,
    CheckinSymptom,
    DiscussionItem,
    LoadEvent,
    Message,
    RecoveryEvent,
    TherapySession,
    User,
)
from recovery.domain import CheckinStatus, CheckinType, DiscussionStatus, InputType
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


def _checkin_lines(c: Checkin, day: Day) -> list[str]:
    lines: list[str] = []
    if c.checkin_type == CheckinType.MORNING:
        head = []
        if c.sleep_quality:
            head.append(f"после сна: {ru.SLEEP_LABELS.get(c.sleep_quality, '?').lower()}")
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
        loads = day.loads.get(c.id, [])
        if loads:
            lines.append(f"Забирало силы: {_labels([e.category for e in loads], ru.LOAD_LABELS)}")
        recs = day.recoveries.get(c.id, [])
        if recs:
            lines.append(f"Помогало: {_labels([e.activity for e in recs], ru.RECOVERY_LABELS)}")
        if c.pleasant_energy:
            lines.append(f"Силы на приятное: {ru.ENERGY_LABELS[c.pleasant_energy].lower()}")
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
            out.extend(f"- {line}" for line in lines)
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
    text = "\n\n".join(
        [
            f"# Неделя: {fmt_period(start, end)}",
            coverage_line(digest),
            "_Только записанные факты, без выводов. Ресурсы показаны по отдельности._",
            *([battery] if battery else []),
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
