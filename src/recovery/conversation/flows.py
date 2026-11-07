"""Check-in flows declared as data.

A flow is an ordered list of questions. The engine asks the first question
that has no status yet; questions in a ``block`` are asked only after their
gate question was answered "yes" (the optional "подробнее" part of the
evening check-in keeps the default path short).
"""

from dataclasses import dataclass
from enum import StrEnum

from recovery.domain import CheckinType
from recovery.texts import ru


class Kind(StrEnum):
    SCALE_10 = "scale10"
    SCALE_5 = "scale5"
    CHOICE = "choice"
    MULTI = "multi"
    TEXT = "text"
    GATE = "gate"


class Target(StrEnum):
    """Where an answer is stored besides a ``checkins`` column."""

    COLUMN = "column"
    SYMPTOMS = "symptoms"
    LOAD_EVENTS = "load_events"
    RECOVERY_EVENTS = "recovery_events"


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Question:
    id: str  # short: it travels in callback data (64-byte limit)
    text: str
    kind: Kind
    target: Target = Target.COLUMN
    column: str | None = None  # for Target.COLUMN
    options: tuple[Option, ...] = ()
    block: str | None = None  # asked only if the gate for this block said yes
    opens: str | None = None  # for GATE questions

    def option_label(self, value: str) -> str:
        for option in self.options:
            if option.value == value:
                return option.label
        return value


@dataclass(frozen=True)
class Flow:
    checkin_type: CheckinType
    intro: str
    outro: str
    questions: tuple[Question, ...]

    def get(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)


def _options(labels: dict[str, str]) -> tuple[Option, ...]:
    return tuple(Option(value, label) for value, label in labels.items())


def _scale(qid: str, text: str, column: str, block: str | None = None) -> Question:
    return Question(qid, text, Kind.SCALE_10, column=column, block=block)


SIGNS_OPTIONS = _options(ru.SYMPTOM_LABELS)
YES_NO = (Option("yes", ru.BTN_YES), Option("no", ru.BTN_NO))

MORNING = Flow(
    checkin_type=CheckinType.MORNING,
    intro=ru.INTRO_MORNING,
    outro=ru.OUTRO_MORNING,
    questions=(
        Question(
            "sleep",
            ru.Q_SLEEP,
            Kind.CHOICE,
            column="sleep_quality",
            options=_options(ru.SLEEP_LABELS),
        ),
        _scale("phys", ru.Q_PHYSICAL_NOW, "physical_score"),
        Question(
            "desire",
            ru.Q_PLEASANT_DESIRE,
            Kind.CHOICE,
            column="pleasant_desire",
            options=_options(ru.DESIRE_LABELS),
        ),
        Question(
            "outlook",
            ru.Q_DAY_OUTLOOK,
            Kind.CHOICE,
            column="day_outlook",
            options=_options(ru.OUTLOOK_LABELS),
        ),
        Question("heavy", ru.Q_EXPECTED_LOAD, Kind.TEXT, column="expected_heavy_load"),
    ),
)

EVENING = Flow(
    checkin_type=CheckinType.EVENING,
    intro=ru.INTRO_EVENING,
    outro=ru.OUTRO_EVENING,
    questions=(
        Question(
            "cmp",
            ru.Q_COMPARISON,
            Kind.CHOICE,
            column="comparison_to_morning",
            options=_options(ru.COMPARISON_LABELS),
        ),
        Question(
            "drain",
            ru.Q_DRAINS,
            Kind.MULTI,
            target=Target.LOAD_EVENTS,
            options=_options({k: v for k, v in ru.LOAD_LABELS.items() if k != "mixed"}),
        ),
        Question(
            "helped",
            ru.Q_HELPED,
            Kind.MULTI,
            target=Target.RECOVERY_EVENTS,
            options=_options(ru.RECOVERY_LABELS),
        ),
        Question(
            "energy",
            ru.Q_PLEASANT_ENERGY,
            Kind.CHOICE,
            column="pleasant_energy",
            options=_options(ru.ENERGY_LABELS),
        ),
        Question("more", ru.Q_MORE_DETAILS, Kind.GATE, options=YES_NO, opens="details"),
        _scale("phys", ru.Q_PHYSICAL, "physical_score", block="details"),
        _scale("emo", ru.Q_EMOTIONAL, "emotional_score", block="details"),
        _scale("soc", ru.Q_SOCIAL, "social_score", block="details"),
        _scale("cog", ru.Q_COGNITIVE, "cognitive_score", block="details"),
        _scale("all", ru.Q_OVERALL, "overall_score", block="details"),
        Question(
            "interest",
            ru.Q_INTEREST,
            Kind.SCALE_5,
            column="interest_score",
            options=_options(ru.INTEREST_LABELS),
            block="details",
        ),
        Question(
            "signs",
            ru.Q_SIGNS,
            Kind.MULTI,
            target=Target.SYMPTOMS,
            options=SIGNS_OPTIONS,
            block="details",
        ),
        Question("tmrw", ru.Q_TOMORROW, Kind.TEXT, column="tomorrow_note"),
    ),
)

AD_HOC = Flow(
    checkin_type=CheckinType.AD_HOC,
    intro=ru.INTRO_AD_HOC,
    outro=ru.OUTRO_AD_HOC,
    questions=(
        _scale("all", ru.Q_OVERALL_NOW, "overall_score"),
        Question(
            "signs",
            ru.Q_SIGNS_NOW,
            Kind.MULTI,
            target=Target.SYMPTOMS,
            options=SIGNS_OPTIONS,
        ),
        Question("note", ru.Q_NOTE_NOW, Kind.TEXT, column="notes"),
    ),
)

FLOWS: dict[str, Flow] = {
    CheckinType.MORNING: MORNING,
    CheckinType.EVENING: EVENING,
    CheckinType.AD_HOC: AD_HOC,
}
