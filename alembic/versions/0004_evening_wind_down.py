"""Evening wind-down: bedtime, walk time, parked thoughts, short evening.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_OLD = (
    "'morning', 'evening', 'morning_reminder', 'evening_reminder', 'session_brief', 'weekly_report'"
)
KINDS_NEW = KINDS_OLD + ", 'pre_walk'"
KIND_CK = "ck_scheduled_prompts_kind_valid"


def upgrade() -> None:
    op.add_column("users", sa.Column("bedtime", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("walk_time", sa.Time(), nullable=True))

    op.add_column("checkins", sa.Column("flow_variant", sa.Text(), nullable=True))
    op.add_column("checkins", sa.Column("bedtime", sa.Time(), nullable=True))
    op.create_check_constraint(
        op.f("ck_checkins_flow_variant_valid"), "checkins", "flow_variant IN ('short')"
    )

    op.drop_constraint(op.f(KIND_CK), "scheduled_prompts", type_="check")
    op.create_check_constraint(op.f(KIND_CK), "scheduled_prompts", f"kind IN ({KINDS_NEW})")

    op.create_table(
        "parked_thoughts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("taken_on", sa.Date(), nullable=True),
        sa.Column(
            "postponed_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "session_suggested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("discussion_item_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tz_name", sa.Text(), nullable=False),
        sa.Column(
            "local_ts",
            sa.DateTime(),
            sa.Computed("created_at_utc AT TIME ZONE tz_name", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "local_date",
            sa.Date(),
            sa.Computed("(created_at_utc AT TIME ZONE tz_name)::date", persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'to_session', 'dropped')",
            name=op.f("ck_parked_thoughts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["discussion_item_id"],
            ["discussion_items.id"],
            name=op.f("fk_parked_thoughts_discussion_item_id_discussion_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_parked_thoughts_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_parked_thoughts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parked_thoughts")),
    )
    op.create_index(
        "ix_parked_thoughts_user_status", "parked_thoughts", ["user_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_parked_thoughts_user_status", table_name="parked_thoughts")
    op.drop_table("parked_thoughts")
    op.execute("DELETE FROM scheduled_prompts WHERE kind = 'pre_walk'")
    op.drop_constraint(op.f(KIND_CK), "scheduled_prompts", type_="check")
    op.create_check_constraint(op.f(KIND_CK), "scheduled_prompts", f"kind IN ({KINDS_OLD})")
    op.drop_constraint(op.f("ck_checkins_flow_variant_valid"), "checkins", type_="check")
    op.drop_column("checkins", "bedtime")
    op.drop_column("checkins", "flow_variant")
    op.drop_column("users", "walk_time")
    op.drop_column("users", "bedtime")
