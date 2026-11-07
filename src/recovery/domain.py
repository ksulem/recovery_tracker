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


class PromptStatus(StrEnum):
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


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
