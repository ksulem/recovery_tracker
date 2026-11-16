""" "Другое — опишу своими словами" in every list, with a note in own words.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'close_people', 'work_social', 'crowd', 'work', 'chores', 'movement', 'hobby', "
    "'leisure', 'solitude', 'rest', 'worries', 'decisions', 'self_demands'"
)
RESOURCES = "'emotional', 'physical', 'social', 'cognitive'"
PRESSURE = "'productivity', 'guilt_resting', 'not_enough_recovery', 'other'"
PRE_SLEEP = "'video', 'shopping', 'social_media'"


def _checks(other: bool) -> dict[tuple[str, str], str]:
    extra = ", 'other'" if other else ""
    return {
        ("day_episodes", "ck_day_episodes_kind_valid"): f"kind IN ({KINDS}{extra})",
        ("day_episodes", "ck_day_episodes_gave_valid"): (
            f"gave <@ ARRAY[{RESOURCES}{extra}]::text[]"
        ),
        ("day_episodes", "ck_day_episodes_took_valid"): (
            f"took <@ ARRAY[{RESOURCES}{extra}]::text[]"
        ),
        ("checkin_marks", "ck_checkin_marks_value_valid"): (
            f"(kind = 'pressure' AND value IN ({PRESSURE})) "
            f"OR (kind = 'pre_sleep' AND value IN ({PRE_SLEEP}{extra}))"
        ),
    }


def _replace_checks(other: bool) -> None:
    for (table, name), condition in _checks(other).items():
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, condition)


def upgrade() -> None:
    for column in ("pre_sleep_note", "signs_note", "sleep_onset_note"):
        op.add_column("checkins", sa.Column(column, sa.Text(), nullable=True))
    for column in ("note", "gave_note", "took_note"):
        op.add_column("day_episodes", sa.Column(column, sa.Text(), nullable=True))
    _replace_checks(other=True)


def downgrade() -> None:
    op.execute("DELETE FROM day_episodes WHERE kind = 'other'")
    op.execute("UPDATE day_episodes SET gave = array_remove(gave, 'other')")
    op.execute("UPDATE day_episodes SET took = array_remove(took, 'other')")
    op.execute("DELETE FROM checkin_marks WHERE kind = 'pre_sleep' AND value = 'other'")
    _replace_checks(other=False)
    for column in ("note", "gave_note", "took_note"):
        op.drop_column("day_episodes", column)
    for column in ("pre_sleep_note", "signs_note", "sleep_onset_note"):
        op.drop_column("checkins", column)
