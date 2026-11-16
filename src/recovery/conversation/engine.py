"""Check-in engine: Telegram-agnostic business logic.

* Every answer is written immediately (no "submit at the end"), so an
  abandoned or interrupted check-in loses nothing.
* A skip leaves the value NULL and marks the question "skipped" in
  ``question_status`` — never 0, never a guess.
* "Закончить" completes the check-in with whatever was answered.
"""

import uuid
from collections.abc import Collection
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery import battery, thoughts
from recovery.clock import local_today, utcnow
from recovery.conversation.flows import (
    WALK_FEATURE,
    Flow,
    Kind,
    Question,
    Target,
    get_flow,
    pick_next,
)
from recovery.db.models import (
    Checkin,
    CheckinMark,
    CheckinSymptom,
    DayEpisode,
    LoadEvent,
    ParkedThought,
    RecoveryEvent,
    User,
)
from recovery.domain import CheckinStatus, CheckinType, MarkKind, Source

ANSWERED = "answered"
SKIPPED = "skipped"
GATE_YES = "yes"
GATE_NO = "no"


class InvalidAnswer(ValueError):
    pass


def flow_for(checkin: Checkin) -> Flow:
    return get_flow(checkin.checkin_type, checkin.flow_variant)


def disabled_features(user: User) -> frozenset[str]:
    disabled = set(user.disabled_features or ())
    if not user.battery_enabled:
        disabled.add(battery.FEATURE)
    if user.walk_time is None:
        disabled.add(WALK_FEATURE)
    return frozenset(disabled)


def checkin_day(checkin: Checkin) -> date:
    return checkin.created_at_utc.astimezone(ZoneInfo(checkin.tz_name)).date()


def next_question(
    checkin: Checkin, skip_features: Collection[str] = frozenset()
) -> Question | None:
    return pick_next(
        flow_for(checkin),
        checkin.question_status,
        checkin.answers or {},
        frozenset(skip_features),
        day=checkin_day(checkin),
        gate_yes=GATE_YES,
    )


def _validate(question: Question, value: str | list[str]) -> str | list[str]:
    allowed = {o.value for o in question.options}
    if question.kind == Kind.MULTI:
        if not isinstance(value, list) or any(v not in allowed for v in value):
            raise InvalidAnswer(question.id)
        if question.none_option is not None and question.none_option in value:
            return [question.none_option]  # "ничего из этого" excludes the rest
        return list(dict.fromkeys(value))  # de-duplicate, keep order
    if not isinstance(value, str):
        raise InvalidAnswer(question.id)
    if question.kind in (Kind.SCALE_10, Kind.SCALE_5):
        high = 10 if question.kind == Kind.SCALE_10 else 5
        if not value.isdigit() or not 0 <= int(value) <= high:
            raise InvalidAnswer(question.id)
    elif question.kind == Kind.PERCENT:
        if not value.isdigit() or not 0 <= int(value) <= 100 or int(value) % 10:
            raise InvalidAnswer(question.id)
    elif question.kind in (Kind.CHOICE, Kind.GATE, Kind.THOUGHT):
        if value not in allowed:
            raise InvalidAnswer(question.id)
    elif question.kind in (Kind.TEXT, Kind.PARK) and not thoughts.split(value):
        raise InvalidAnswer(question.id)
    return value


class CheckinEngine:
    def __init__(self, session: AsyncSession, user: User) -> None:
        self.session = session
        self.user = user

    async def get(self, checkin_id: uuid.UUID | str) -> Checkin | None:
        cid = checkin_id if isinstance(checkin_id, uuid.UUID) else uuid.UUID(checkin_id)
        checkin = await self.session.get(Checkin, cid)
        if checkin is None or checkin.user_id != self.user.id:
            return None
        return checkin

    async def find_today(self, checkin_type: CheckinType) -> Checkin | None:
        """Today's live (not abandoned) morning/evening check-in, if any."""
        today = local_today(self.user.timezone)
        return await self.session.scalar(
            select(Checkin).where(
                Checkin.user_id == self.user.id,
                Checkin.checkin_type == checkin_type,
                Checkin.local_date == today,
                Checkin.status != CheckinStatus.ABANDONED,
            )
        )

    async def start(self, checkin_type: CheckinType, variant: str | None = None) -> Checkin:
        checkin = Checkin(
            user_id=self.user.id,
            checkin_type=checkin_type,
            status=CheckinStatus.IN_PROGRESS,
            flow_variant=variant,
            # Snapshot, to report later how long before sleep the day review was.
            bedtime=self.user.bedtime if checkin_type == CheckinType.EVENING else None,
            tz_name=self.user.timezone,
            created_at_utc=utcnow(),
            question_status={},
            answers={},
        )
        self.session.add(checkin)
        await self.session.flush()
        return checkin

    async def abandon(self, checkin: Checkin) -> None:
        checkin.status = CheckinStatus.ABANDONED
        await self.session.flush()

    async def skip(self, checkin: Checkin, question: Question) -> None:
        self._mark(checkin, question, GATE_NO if question.kind == Kind.GATE else SKIPPED)
        await self.session.flush()

    async def answer(
        self, checkin: Checkin, question: Question, raw: str | list[str]
    ) -> ParkedThought | None:
        """Store an answer. Returns a parked thought only when the owner just
        postponed it for the third time — the moment to offer the session list."""
        value = _validate(question, raw)
        suggest: ParkedThought | None = None
        if question.kind == Kind.PARK:
            assert isinstance(value, str)
            await thoughts.park(self.session, self.user, value)
            self._mark(checkin, question, ANSWERED)
        elif question.kind == Kind.THOUGHT:
            assert isinstance(value, str)
            thought = await thoughts.walk_thought_today(self.session, self.user)
            if thought is not None and await thoughts.resolve(
                self.session, self.user, thought, thoughts.Action(value)
            ):
                suggest = thought
            self._mark(checkin, question, ANSWERED)
        elif question.kind == Kind.GATE:
            self._remember(checkin, question, value)
            self._mark(checkin, question, GATE_YES if value == "yes" else GATE_NO)
        elif question.kind == Kind.MULTI:
            assert isinstance(value, list)
            if not value:
                self._mark(checkin, question, SKIPPED)
            else:
                chosen = [v for v in value if v != question.none_option]
                self._remember(checkin, question, value)
                await self._store_many(checkin, question, chosen)
                self._mark(checkin, question, ANSWERED)
        else:
            assert isinstance(value, str)
            if question.kind != Kind.TEXT:  # raw choices only: free text stays in its column
                self._remember(checkin, question, value)
            if question.column is None:  # stored elsewhere (see _store_one)
                await self._store_one(checkin, question, value)
                self._mark(checkin, question, ANSWERED)
                await self.session.flush()
                return suggest
            numeric = question.kind in (Kind.SCALE_10, Kind.SCALE_5, Kind.PERCENT)
            stored: str | int = int(value) if numeric else value
            if question.kind == Kind.TEXT:
                stored = self._append(getattr(checkin, question.column), value.strip())
            setattr(checkin, question.column, stored)
            if question.kind == Kind.PERCENT:
                # Snapshot: a later change of the reserve doesn't rewrite history.
                checkin.battery_reserve_pct = self.user.battery_reserve_pct
            self._mark(checkin, question, ANSWERED)
        await self.session.flush()
        return suggest

    async def add_note(self, checkin: Checkin, text: str) -> None:
        checkin.notes = self._append(checkin.notes, text.strip())
        await self.session.flush()

    async def finish(self, checkin: Checkin) -> None:
        checkin.status = CheckinStatus.COMPLETED
        checkin.completed_at_utc = utcnow()
        await self.session.flush()

    # --- helpers ---------------------------------------------------------------
    @staticmethod
    def _mark(checkin: Checkin, question: Question, status: str) -> None:
        # Reassign (not mutate) so SQLAlchemy sees the JSONB change.
        checkin.question_status = {**checkin.question_status, question.id: status}

    @staticmethod
    def _remember(checkin: Checkin, question: Question, value: str | list[str]) -> None:
        """Raw choice answers, for conditional and per-item questions."""
        checkin.answers = {**(checkin.answers or {}), question.id: value}

    async def _episode(self, checkin: Checkin, kind: str | None) -> DayEpisode | None:
        if kind is None:
            return None
        return await self.session.scalar(
            select(DayEpisode).where(DayEpisode.checkin_id == checkin.id, DayEpisode.kind == kind)
        )

    async def _store_one(self, checkin: Checkin, question: Question, value: str) -> None:
        """Single-choice answers that live outside ``checkins`` columns."""
        episode = await self._episode(checkin, question.item)
        if episode is None:
            return
        if question.target == Target.EPISODE_EFFECT:
            episode.effect = value
        elif question.target == Target.EPISODE_CHOICE:
            episode.choice = value

    @staticmethod
    def _append(existing: str | None, text: str) -> str:
        return f"{existing}\n{text}" if existing else text

    async def _store_many(self, checkin: Checkin, question: Question, values: list[str]) -> None:
        common = {"tz_name": checkin.tz_name, "created_at_utc": utcnow()}
        if question.target in (Target.EPISODE_GAVE, Target.EPISODE_TOOK):
            episode = await self._episode(checkin, question.item)
            if episode is not None:
                if question.target == Target.EPISODE_GAVE:
                    episode.gave = list(values)
                else:
                    episode.took = list(values)
            return
        for value in values:
            if question.target == Target.EPISODES:
                self.session.add(
                    DayEpisode(
                        user_id=self.user.id,
                        checkin_id=checkin.id,
                        kind=value,
                        source=Source.BUTTON,
                        **common,
                    )
                )
            elif question.target in (Target.PRESSURE, Target.PRE_SLEEP):
                pressure = question.target == Target.PRESSURE
                mark = MarkKind.PRESSURE if pressure else MarkKind.PRE_SLEEP
                self.session.add(CheckinMark(checkin_id=checkin.id, kind=mark, value=value))
            elif question.target == Target.SYMPTOMS:
                self.session.add(
                    CheckinSymptom(checkin_id=checkin.id, symptom=value, source=Source.BUTTON)
                )
            elif question.target == Target.LOAD_EVENTS:
                self.session.add(
                    LoadEvent(
                        user_id=self.user.id,
                        checkin_id=checkin.id,
                        category=value,
                        source=Source.BUTTON,
                        **common,
                    )
                )
            elif question.target == Target.RECOVERY_EVENTS:
                self.session.add(
                    RecoveryEvent(
                        user_id=self.user.id,
                        checkin_id=checkin.id,
                        activity=value,
                        source=Source.BUTTON,
                        **common,
                    )
                )
