"""Battery exercise: per-user reserve/colour/toggle, per-check-in charge.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLORS = "'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'brown'"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("battery_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column("users", sa.Column("battery_reserve_pct", sa.SmallInteger(), nullable=True))
    op.add_column(
        "users",
        sa.Column("battery_color", sa.Text(), server_default=sa.text("'green'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_users_battery_reserve_pct_range"),
        "users",
        "battery_reserve_pct BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        op.f("ck_users_battery_color_valid"), "users", f"battery_color IN ({COLORS})"
    )
    op.add_column("checkins", sa.Column("battery_pct", sa.SmallInteger(), nullable=True))
    op.add_column("checkins", sa.Column("battery_reserve_pct", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_checkins_battery_pct_range"), "checkins", "battery_pct BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        op.f("ck_checkins_battery_reserve_pct_range"),
        "checkins",
        "battery_reserve_pct BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_checkins_battery_reserve_pct_range"), "checkins", type_="check")
    op.drop_constraint(op.f("ck_checkins_battery_pct_range"), "checkins", type_="check")
    op.drop_column("checkins", "battery_reserve_pct")
    op.drop_column("checkins", "battery_pct")
    op.drop_constraint(op.f("ck_users_battery_color_valid"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_battery_reserve_pct_range"), "users", type_="check")
    op.drop_column("users", "battery_color")
    op.drop_column("users", "battery_reserve_pct")
    op.drop_column("users", "battery_enabled")
