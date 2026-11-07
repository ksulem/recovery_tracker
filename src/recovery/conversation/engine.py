"""Check-in engine: Telegram-agnostic business logic.

* Every answer is written immediately (no "submit at the end"), so an
  abandoned or interrupted check-in loses nothing.
* A skip leaves the value NULL and marks the question "skipped" in
  ``question_status`` — never 0, never a guess.
* "Закончить" completes the check-in with whatever was answered.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import local_today, utcnow
from recovery.conversation.flows import FLOWS, Flow, Kind, Question, Target
from recovery.db.models import Checkin, CheckinSymptom, LoadEvent, RecoveryEvent, User
from recovery.domain import CheckinStatus, CheckinType, Source

ANSWERED = "answered"
SKIPPED = "skipped"
GATE_YES = "yes"
GATE_NO = "no"


class InvalidAnswer(ValueError):
    pass


def flow_for(checkin: Checkin) -> Flow:
    return FLOWS[checkin.checkin_type]


def next_question(checkin: Checkin) -> Question | None:
    status = checkin.question_status
    flow = flow_for(checkin)
    open_blocks = {
        q.opens for q in flow.questions if q.kind == Kind.GATE and status.get(q.id) == GATE_YES
    }
    for question in flow.questions:
        if question.id in status:
            continue
        if question.block and question.block not in open_blocks:
            # The gate comes before its block in the flow, so by now it was
            # answered "no" or skipped: leave the optional block out.
            continue
        return question
    return None


def _validate(question: Question, value: str | list[str]) -> str | list[str]:
    allowed = {o.value for o in question.options}
    if question.kind == Kind.MULTI:
        if not isinstance(value, list) or any(v not in allowed for v in value):
            raise InvalidAnswer(question.id)
        return list(dict.fromkeys(value))  # de-duplicate, keep order
    if not isinstance(value, str):
        raise InvalidAnswer(question.id)
    if question.kind in (Kind.SCALE_10, Kind.SCALE_5):
        high = 10 if question.kind == Kind.SCALE_10 else 5
        if not value.isdigit() or not 0 <= int(value) <= high:
            raise InvalidAnswer(question.id)
    elif question.kind in (Kind.CHOICE, Kind.GATE):
        if value not in allowed:
            raise InvalidAnswer(question.id)
    elif question.kind == Kind.TEXT and not value.strip():
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

    async def start(self, checkin_type: CheckinType) -> Checkin:
        checkin = Checkin(
            user_id=self.user.id,
            checkin_type=checkin_type,
            status=CheckinStatus.IN_PROGRESS,
            tz_name=self.user.timezone,
            created_at_utc=utcnow(),
            question_status={},
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

    async def answer(self, checkin: Checkin, question: Question, raw: str | list[str]) -> None:
        value = _validate(question, raw)
        if question.kind == Kind.GATE:
            self._mark(checkin, question, GATE_YES if value == "yes" else GATE_NO)
        elif question.kind == Kind.MULTI:
            assert isinstance(value, list)
            if not value:
                self._mark(checkin, question, SKIPPED)
            else:
                self._store_many(checkin, question, value)
                self._mark(checkin, question, ANSWERED)
        else:
            assert isinstance(value, str) and question.column
            stored: str | int = (
                int(value) if question.kind in (Kind.SCALE_10, Kind.SCALE_5) else value
            )
            if question.kind == Kind.TEXT:
                stored = self._append(getattr(checkin, question.column), value.strip())
            setattr(checkin, question.column, stored)
            self._mark(checkin, question, ANSWERED)
        await self.session.flush()

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
    def _append(existing: str | None, text: str) -> str:
        return f"{existing}\n{text}" if existing else text

    def _store_many(self, checkin: Checkin, question: Question, values: list[str]) -> None:
        common = {"tz_name": checkin.tz_name, "created_at_utc": utcnow()}
        for value in values:
            if question.target == Target.SYMPTOMS:
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
