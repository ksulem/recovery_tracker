"""Pressure on oneself: an "Другое" option with a note in the owner's own words.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CK = "ck_checkin_marks_value_valid"
PRE_SLEEP = "'video', 'shopping', 'social_media'"


def _marks_check(pressure: str) -> str:
    return (
        f"(kind = 'pressure' AND value IN ({pressure})) "
        f"OR (kind = 'pre_sleep' AND value IN ({PRE_SLEEP}))"
    )


OLD = _marks_check("'productivity', 'guilt_resting', 'not_enough_recovery'")
NEW = _marks_check("'productivity', 'guilt_resting', 'not_enough_recovery', 'other'")


def upgrade() -> None:
    op.add_column("checkins", sa.Column("pressure_note", sa.Text(), nullable=True))
    op.drop_constraint(op.f(CK), "checkin_marks", type_="check")
    op.create_check_constraint(op.f(CK), "checkin_marks", NEW)


def downgrade() -> None:
    op.execute("DELETE FROM checkin_marks WHERE kind = 'pressure' AND value = 'other'")
    op.drop_constraint(op.f(CK), "checkin_marks", type_="check")
    op.create_check_constraint(op.f(CK), "checkin_marks", OLD)
    op.drop_column("checkins", "pressure_note")
