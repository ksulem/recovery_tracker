"""Closed vocabularies. Stored as TEXT with CHECK constraints built from these
enums, so adding a value is a one-line change plus a migration."""

from enum import StrEnum


class CheckinType(StrEnum):
    MORNING = "morning"
    EVENING = "evening"
    AD_HOC = "ad_hoc"


class CheckinStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SleepQuality(StrEnum):
    POOR = "poor"
    MEDIUM = "medium"
    GOOD = "good"
    UNKNOWN = "unknown"


class Comparison(StrEnum):
    BETTER = "better"
    SAME = "same"
    WORSE = "worse"
    UNKNOWN = "unknown"


class DayOutlook(StrEnum):
    EASY = "easy"
    TOLERABLE = "tolerable"
    VERY_HARD = "very_hard"


class PleasantDesire(StrEnum):
    NO = "no"
    A_LITTLE = "a_little"
    YES = "yes"


class PleasantEnergy(StrEnum):
    DID_IT = "did_it"
    WANTED_NO_ENERGY = "wanted_no_energy"
    NOTHING_WANTED = "nothing_wanted"
    UNKNOWN = "unknown"


class LoadCategory(StrEnum):
    WORK = "work"
    EMOTIONAL = "emotional"
    SOCIAL = "social"
    PHYSICAL = "physical"
    DECISIONS = "decisions"
    MIXED = "mixed"


class Intensity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class PartOfDay(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"
    UNKNOWN = "unknown"


class RecoveryActivity(StrEnum):
    ALONE_AT_HOME = "alone_at_home"
    SLEEP_REST = "sleep_rest"
    SHOWS_READING_GAMES = "shows_reading_games"
    HOBBY = "hobby"
    WALK = "walk"
    CLOSE_PEOPLE = "close_people"
    LIGHT_CHORES = "light_chores"
    OTHER = "other"


class Effect(StrEnum):
    BETTER = "better"
    SAME = "same"
    WORSE = "worse"
    UNKNOWN = "unknown"


class Symptom(StrEnum):
    NEED_ISOLATION = "need_isolation"
    NO_MOTIVATION = "no_motivation"
    IRRITABILITY = "irritability"
    LOW_CONCENTRATION = "low_concentration"
    PHYSICAL_FATIGUE = "physical_fatigue"
    SLEEP_DIFFICULTY = "sleep_difficulty"
    EMOTIONAL_INSTABILITY = "emotional_instability"
    PLEASANT_FEELS_HEAVY = "pleasant_feels_heavy"
    STARING_BLANKLY = "staring_blankly"
    OTHER = "other"


class Source(StrEnum):
    BUTTON = "button"
    LLM = "llm"
    MANUAL = "manual"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InputType(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    BUTTON = "button"


class PromptKind(StrEnum):
    MORNING = "morning"
    EVENING = "evening"
    MORNING_REMINDER = "morning_reminder"
    EVENING_REMINDER = "evening_reminder"
    SESSION_BRIEF = "session_brief"
    WEEKLY_REPORT = "weekly_report"
    PRE_WALK = "pre_walk"  # offer to take one parked thought on the evening walk


class PromptStatus(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # not needed: e.g. the check-in was already started by hand
    CANCELLED = "cancelled"  # the owner tapped "Не сейчас" — no reminder after that


class DiscussionStatus(StrEnum):
    OPEN = "open"
    DISCUSSED = "discussed"
    DROPPED = "dropped"


class ConsentKind(StrEnum):
    LLM = "llm"
    STT = "stt"


class SafetySource(StrEnum):
    KEYWORD = "keyword"
    LLM = "llm"


class BatteryColor(StrEnum):
    """The "battery" exercise: the reserve is pictured in a favourite colour."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    BROWN = "brown"


class FlowVariant(StrEnum):
    SHORT = "short"  # late-evening check-in: battery + what to park


class ThoughtStatus(StrEnum):
    OPEN = "open"
    DONE = "done"  # обдумано
    TO_SESSION = "to_session"  # moved to the list for the psychologist session
    DROPPED = "dropped"  # неактуально


class EpisodeKind(StrEnum):
    """What happened during the day ("эпизоды дня"). Social contact is split
    on purpose: "общение" both drained and recharged in week 1."""

    CLOSE_PEOPLE = "close_people"
    WORK_SOCIAL = "work_social"  # по работе или обязательное
    CROWD = "crowd"  # много людей, новые люди
    WORK = "work"
    CHORES = "chores"
    MOVEMENT = "movement"
    HOBBY = "hobby"
    LEISURE = "leisure"  # сериалы, книги, игры
    SOLITUDE = "solitude"
    REST = "rest"
    WORRIES = "worries"
    DECISIONS = "decisions"
    SELF_DEMANDS = "self_demands"  # требования к себе
    OTHER = "other"  # своими словами (day_episodes.note)


class EpisodeEffect(StrEnum):
    GAVE = "gave"
    TOOK = "took"
    BOTH = "both"
    NEUTRAL = "neutral"


class Resource(StrEnum):
    """The spec's separate resources (never averaged)."""

    EMOTIONAL = "emotional"
    PHYSICAL = "physical"
    SOCIAL = "social"
    COGNITIVE = "cognitive"
    OTHER = "other"  # своими словами (day_episodes.gave_note / took_note)


class EpisodeChoice(StrEnum):
    WANTED = "wanted"  # по своему желанию
    NEEDED = "needed"  # по необходимости


class Obligations(StrEnum):
    NONE = "none"
    SOME = "some"
    MANY = "many"


class Preoccupation(StrEnum):
    NONE = "none"
    SOME = "some"
    STRONG = "strong"


class SleepOnset(StrEnum):
    EASY = "easy"
    THOUGHTS = "thoughts"  # долго крутились мысли
    OTHER = "other"


class PressureSignal(StrEnum):
    PRODUCTIVITY = "productivity"  # надо было быть продуктивнее
    GUILT_RESTING = "guilt_resting"  # вина за отдых
    NOT_ENOUGH_RECOVERY = "not_enough_recovery"  # недостаточно стараюсь восстановиться
    OTHER = "other"  # своими словами (checkins.pressure_note)


class PreSleepActivity(StrEnum):
    VIDEO = "video"
    SHOPPING = "shopping"  # покупки или каталоги
    SOCIAL_MEDIA = "social_media"
    OTHER = "other"  # своими словами (checkins.pre_sleep_note)


class MarkKind(StrEnum):
    PRESSURE = "pressure"
    PRE_SLEEP = "pre_sleep"
