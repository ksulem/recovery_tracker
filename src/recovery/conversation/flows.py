"""Check-in flows declared as data.

A flow is an ordered list of questions. The engine asks the first question
that has no status yet and whose conditions hold:

* ``block`` — asked only after its gate question was answered "yes" (the
  optional "подробнее" part of the evening check-in);
* ``feature`` — asked only if that optional feature/question set is on;
* ``when`` — asked only if an earlier answer contains one of the given values;
* ``every_days`` — asked only on every N-th local day (e.g. every other evening);
* ``each`` — a *template* repeated for every item selected in a multi-select
  question (at most ``max_items`` of the source), e.g. "как это было?" per
  episode. Consecutive templates over the same source form a group and are
  asked item by item. Instance ids are ``"<template>.<item>"``; ``{item}`` in
  the text and in ``when`` is replaced by the item's label / value.
"""

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from recovery import battery
from recovery.domain import CheckinType, FlowVariant
from recovery.texts import ru


class Kind(StrEnum):
    SCALE_10 = "scale10"
    SCALE_5 = "scale5"
    PERCENT = "percent"  # 0–100 in steps of 10 (the battery charge)
    CHOICE = "choice"
    MULTI = "multi"
    TEXT = "text"
    GATE = "gate"
    PARK = "park"  # free text → parked thoughts, one per line
    THOUGHT = "thought"  # what to do with the thought taken on today's walk


class Target(StrEnum):
    """Where an answer is stored besides a ``checkins`` column."""

    COLUMN = "column"
    SYMPTOMS = "symptoms"
    LOAD_EVENTS = "load_events"  # legacy: before day episodes
    RECOVERY_EVENTS = "recovery_events"  # legacy: before day episodes
    EPISODES = "episodes"  # one day_episodes row per selected item
    EPISODE_EFFECT = "episode_effect"
    EPISODE_GAVE = "episode_gave"
    EPISODE_TOOK = "episode_took"
    EPISODE_CHOICE = "episode_choice"
    EPISODE_NOTE = "episode_note"  # own words for the "Другое" episode
    EPISODE_GAVE_NOTE = "episode_gave_note"
    EPISODE_TOOK_NOTE = "episode_took_note"
    PRESSURE = "pressure"  # checkin_marks
    PRE_SLEEP = "pre_sleep"  # checkin_marks


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
    feature: str | None = None  # asked only if this optional feature is enabled
    when: tuple[str, frozenset[str]] | None = None  # (question id, any of these values)
    every_days: int | None = None  # asked only when local_date.toordinal() % N == 0
    # Multi-select: an exclusive "ничего из этого" option — answered, not skipped.
    none_option: str | None = None
    max_items: int | None = None  # multi-select: how many items templates expand to
    each: str | None = None  # template: repeat for each item selected in this question
    items: frozenset[str] | None = None  # template: only for these items
    item: str | None = None  # set on an expanded instance

    def option_label(self, value: str) -> str:
        for option in self.options:
            if option.value == value:
                return option.label
        return value

    def instance(self, item: str, label: str) -> "Question":
        when = self.when
        if when is not None:
            when = (when[0].format(item=item), when[1])
        return replace(
            self,
            id=f"{self.id}.{item}",
            text=self.text.format(item=label),
            each=None,
            items=None,
            item=item,
            when=when,
        )


@dataclass(frozen=True)
class Flow:
    checkin_type: CheckinType
    intro: str
    outro: str
    questions: tuple[Question, ...]

    def get(self, question_id: str) -> Question | None:
        """A question by id, including expanded template instances."""
        base, _, item = question_id.partition(".")
        question = next((q for q in self.questions if q.id == base), None)
        if question is None or not item:
            return question if question is not None and question.each is None else None
        source = self.get(question.each) if question.each else None
        if source is None or item not in {o.value for o in source.options}:
            return None
        return question.instance(item, _item_label(source, item))

    def expanded(self, answers: dict[str, object]) -> list[Question]:
        """The flow with templates expanded for the items selected so far."""
        out: list[Question] = []
        questions = self.questions
        i = 0
        while i < len(questions):
            first = questions[i]
            if first.each is None:
                out.append(first)
                i += 1
                continue
            group = [first]
            while i + len(group) < len(questions) and questions[i + len(group)].each == first.each:
                group.append(questions[i + len(group)])
            i += len(group)
            source = self.get(first.each)
            selected = answers.get(first.each)
            if source is None or not isinstance(selected, list):
                continue
            chosen = [v for v in selected if v != source.none_option]
            for item in chosen[: source.max_items]:
                for template in group:
                    if template.items is None or item in template.items:
                        out.append(template.instance(item, _item_label(source, item)))
        return out


def _item_label(source: Question, item: str) -> str:
    """Label used inside per-item questions: just "Другое" for the own-words option."""
    return "Другое" if item == "other" else source.option_label(item)


def _matches(answer: object, values: frozenset[str]) -> bool:
    if isinstance(answer, list):
        return any(v in values for v in answer)
    return isinstance(answer, str) and answer in values


def pick_next(
    flow: Flow,
    status: dict[str, str],
    answers: dict[str, object],
    skip_features: frozenset[str] | set[str] = frozenset(),
    day: date | None = None,
    gate_yes: str = "yes",
) -> Question | None:
    """The next question to ask — pure, so the rules are easy to test."""
    questions = flow.expanded(answers)
    open_blocks = {
        q.opens for q in questions if q.kind == Kind.GATE and status.get(q.id) == gate_yes
    }
    for question in questions:
        if question.id in status:
            continue
        if question.feature and question.feature in skip_features:
            continue
        if question.block and question.block not in open_blocks:
            # The gate comes before its block in the flow, so by now it was
            # answered "no" or skipped: leave the optional block out.
            continue
        if question.when is not None and not _matches(
            answers.get(question.when[0]), question.when[1]
        ):
            continue
        if question.every_days and day is not None and day.toordinal() % question.every_days:
            continue
        return question
    return None


def _options(labels: dict[str, str]) -> tuple[Option, ...]:
    return tuple(Option(value, label) for value, label in labels.items())


WALK_FEATURE = "walk"

# Optional question sets the owner can switch off in /settings →
# "Дополнительные вопросы": feature name → (button label, description).
HYPOTHESES_FEATURE = "hypotheses"
SLEEP_FEATURE = "sleep"
FEATURE_SETS: dict[str, tuple[str, str]] = {
    HYPOTHESES_FEATURE: (ru.FEATURE_HYPOTHESES, ru.FEATURE_HYPOTHESES_HELP),
    SLEEP_FEATURE: (ru.FEATURE_SLEEP, ru.FEATURE_SLEEP_HELP),
}

MAX_RATED_EPISODES = 3  # "как это было?" for at most three episodes an evening
SOCIAL_AND_CHORES = frozenset({"close_people", "work_social", "crowd", "chores"})

THOUGHT_ACTIONS = (
    Option("done", ru.BTN_THOUGHT_DONE),
    Option("later", ru.BTN_THOUGHT_LATER),
    Option("session", ru.BTN_THOUGHT_SESSION),
    Option("drop", ru.BTN_THOUGHT_DROP),
)

# Closing of the day: the walk thought (only if one was taken today), then
# "anything else to park until tomorrow?".
WALK_THOUGHT = Question(
    "walk", ru.Q_WALK_THOUGHT, Kind.THOUGHT, options=THOUGHT_ACTIONS, feature=WALK_FEATURE
)
PARK = Question("park", ru.Q_PARK, Kind.PARK)


OTHER = frozenset({"other"})


def _other_note(qid: str, text: str, after: str, **kw: object) -> Question:
    """'Другое — опишу своими словами' was picked in ``after``: ask what exactly."""
    return Question(qid, text, Kind.TEXT, when=(after, OTHER), **kw)  # type: ignore[arg-type]


def _battery(text: str) -> Question:
    return Question("battery", text, Kind.PERCENT, column="battery_pct", feature=battery.FEATURE)


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
        Question(
            "onset",
            ru.Q_SLEEP_ONSET,
            Kind.CHOICE,
            column="sleep_onset",
            options=_options(ru.SLEEP_ONSET_LABELS),
            feature=SLEEP_FEATURE,
        ),
        _other_note(
            "onsetnote", ru.Q_ONSET_NOTE, "onset", column="sleep_onset_note", feature=SLEEP_FEATURE
        ),
        Question(
            "presleep",
            ru.Q_PRE_SLEEP,
            Kind.MULTI,
            target=Target.PRE_SLEEP,
            options=_options(ru.PRE_SLEEP_LABELS),
            none_option="none",
            feature=SLEEP_FEATURE,
        ),
        _other_note(
            "psnote",
            ru.Q_PRE_SLEEP_NOTE,
            "presleep",
            column="pre_sleep_note",
            feature=SLEEP_FEATURE,
        ),
        _battery(ru.Q_BATTERY_MORNING),
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
        _battery(ru.Q_BATTERY_NOW),
        Question(
            "oblig",
            ru.Q_OBLIGATIONS,
            Kind.CHOICE,
            column="obligations",
            options=_options(ru.OBLIGATIONS_LABELS),
            feature=HYPOTHESES_FEATURE,
        ),
        # Day episodes: one list, then "how was it?" per item (instead of two
        # whole-day lists "drained" / "helped", which couldn't be interpreted
        # when the same thing appeared in both).
        Question(
            "ep",
            ru.Q_EPISODES,
            Kind.MULTI,
            target=Target.EPISODES,
            options=_options(ru.EPISODE_LABELS),
            none_option="none",
            max_items=MAX_RATED_EPISODES,
        ),
        _other_note("epnote", ru.Q_EPISODE_OTHER_NOTE, "ep", target=Target.EPISODE_NOTE),
        Question(
            "rate",
            ru.Q_EPISODE_RATE,
            Kind.CHOICE,
            target=Target.EPISODE_EFFECT,
            options=_options(ru.EFFECT_LABELS),
            each="ep",
        ),
        Question(
            "gave",
            ru.Q_EPISODE_GAVE,
            Kind.MULTI,
            target=Target.EPISODE_GAVE,
            options=_options(ru.RESOURCE_LABELS),
            each="ep",
            when=("rate.{item}", frozenset({"both"})),
        ),
        Question(
            "gnote",
            ru.Q_GAVE_NOTE,
            Kind.TEXT,
            target=Target.EPISODE_GAVE_NOTE,
            each="ep",
            when=("gave.{item}", OTHER),
        ),
        Question(
            "took",
            ru.Q_EPISODE_TOOK,
            Kind.MULTI,
            target=Target.EPISODE_TOOK,
            options=_options(ru.RESOURCE_LABELS),
            each="ep",
            when=("rate.{item}", frozenset({"both"})),
        ),
        Question(
            "tnote",
            ru.Q_TOOK_NOTE,
            Kind.TEXT,
            target=Target.EPISODE_TOOK_NOTE,
            each="ep",
            when=("took.{item}", OTHER),
        ),
        Question(
            "why",
            ru.Q_EPISODE_CHOICE,
            Kind.CHOICE,
            target=Target.EPISODE_CHOICE,
            options=_options(ru.CHOICE_LABELS),
            each="ep",
            items=SOCIAL_AND_CHORES,
            feature=HYPOTHESES_FEATURE,
        ),
        Question(
            "preocc",
            ru.Q_PREOCCUPATION,
            Kind.CHOICE,
            column="preoccupation",
            options=_options(ru.PREOCCUPATION_LABELS),
            feature=HYPOTHESES_FEATURE,
        ),
        Question(
            "pnote",
            ru.Q_PREOCCUPATION_NOTE,
            Kind.TEXT,
            column="preoccupation_note",
            feature=HYPOTHESES_FEATURE,
            when=("preocc", frozenset({"some", "strong"})),
        ),
        Question(
            "energy",
            ru.Q_PLEASANT_ENERGY,
            Kind.CHOICE,
            column="pleasant_energy",
            options=_options(ru.ENERGY_LABELS),
        ),
        Question(
            "press",
            ru.Q_PRESSURE,
            Kind.MULTI,
            target=Target.PRESSURE,
            options=_options(ru.PRESSURE_LABELS),
            none_option="none",
            every_days=2,
            feature=HYPOTHESES_FEATURE,
        ),
        Question(
            "pressnote",
            ru.Q_PRESSURE_NOTE,
            Kind.TEXT,
            column="pressure_note",
            feature=HYPOTHESES_FEATURE,
            when=("press", frozenset({"other"})),
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
        _other_note("snote", ru.Q_SIGNS_NOTE, "signs", column="signs_note", block="details"),
        Question("tmrw", ru.Q_TOMORROW, Kind.TEXT, column="tomorrow_note"),
        WALK_THOUGHT,
        PARK,
    ),
)

AD_HOC = Flow(
    checkin_type=CheckinType.AD_HOC,
    intro=ru.INTRO_AD_HOC,
    outro=ru.OUTRO_AD_HOC,
    questions=(
        _battery(ru.Q_BATTERY_NOW),
        _scale("all", ru.Q_OVERALL_NOW, "overall_score"),
        Question(
            "signs",
            ru.Q_SIGNS_NOW,
            Kind.MULTI,
            target=Target.SYMPTOMS,
            options=SIGNS_OPTIONS,
        ),
        _other_note("snote", ru.Q_SIGNS_NOTE, "signs", column="signs_note"),
        Question("note", ru.Q_NOTE_NOW, Kind.TEXT, column="notes"),
    ),
)

# Late-evening version: no day review, just the charge and what to park.
EVENING_SHORT = Flow(
    checkin_type=CheckinType.EVENING,
    intro=ru.INTRO_EVENING_SHORT,
    outro=ru.OUTRO_EVENING_SHORT,
    questions=(_battery(ru.Q_BATTERY_NOW), PARK),
)

FLOWS: dict[str, Flow] = {
    CheckinType.MORNING: MORNING,
    CheckinType.EVENING: EVENING,
    CheckinType.AD_HOC: AD_HOC,
}
VARIANTS: dict[tuple[str, str], Flow] = {
    (CheckinType.EVENING, FlowVariant.SHORT): EVENING_SHORT,
}


def get_flow(checkin_type: str, variant: str | None = None) -> Flow:
    if variant is not None:
        return VARIANTS[(checkin_type, variant)]
    return FLOWS[checkin_type]
