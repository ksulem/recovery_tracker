"""Logging with redaction.

Spec §12.5: full messages and transcripts must never reach technical logs.
Our own code only logs IDs, but two things can still leak text:

* ``extra={...}`` fields with message content added by mistake, and
* exception messages — e.g. pydantic validation errors embed ``input_value='...'``.

The filter drops known content-bearing record attributes; the formatter scrubs
quoted values after known keys in messages and tracebacks.
"""

import logging
import re

SENSITIVE_ATTRS = frozenset({"text", "transcript", "caption", "note", "notes", "content"})
REDACTED = "[redacted]"

# key='value' / key="value" / key: 'value' for content-bearing keys.
_QUOTED_VALUE = re.compile(
    r"""(?P<key>\b(?:input_value|text|transcript|caption|notes?|content)\b['"]?\s*[=:]\s*)"""
    r"""(?P<q>['"])(?:\\.|(?!(?P=q)).)*(?P=q)""",
    re.DOTALL,
)


def scrub(value: str) -> str:
    return _QUOTED_VALUE.sub(lambda m: f"{m.group('key')}{REDACTED}", value)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for attr in SENSITIVE_ATTRS & record.__dict__.keys():
            setattr(record, attr, REDACTED)
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))

    def formatException(self, ei):  # type: ignore[no-untyped-def]
        return scrub(super().formatException(ei))


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # aiogram logs every handled update at INFO; IDs only, but noisy.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
