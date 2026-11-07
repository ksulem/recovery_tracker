"""Scheduled prompts: add 'skipped' and 'cancelled' statuses.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ck_scheduled_prompts_status_valid"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), "scheduled_prompts", type_="check")
    op.create_check_constraint(
        op.f(NAME),
        "scheduled_prompts",
        "status IN ('sending', 'sent', 'failed', 'skipped', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM scheduled_prompts WHERE status IN ('skipped', 'cancelled')")
    op.drop_constraint(op.f(NAME), "scheduled_prompts", type_="check")
    op.create_check_constraint(
        op.f(NAME), "scheduled_prompts", "status IN ('sending', 'sent', 'failed')"
    )
