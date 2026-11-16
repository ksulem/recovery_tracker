"""Day episodes (instead of the two "drained / helped" lists) and the week-2
hypothesis and sleep questions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15 01:32:36.992004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKIN_CHECKS = {
    "obligations": "'none', 'some', 'many'",
    "preoccupation": "'none', 'some', 'strong'",
    "sleep_onset": "'easy', 'thoughts', 'other'",
}


def upgrade() -> None:
    op.create_table(
        "checkin_marks",
        sa.Column("checkin_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'pressure' AND value IN ('productivity', 'guilt_resting', 'not_enough_recovery')) OR (kind = 'pre_sleep' AND value IN ('video', 'shopping', 'social_media'))",
            name=op.f("ck_checkin_marks_value_valid"),
        ),
        sa.CheckConstraint(
            "kind IN ('pressure', 'pre_sleep')", name=op.f("ck_checkin_marks_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["checkin_id"],
            ["checkins.id"],
            name=op.f("fk_checkin_marks_checkin_id_checkins"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("checkin_id", "kind", "value", name=op.f("pk_checkin_marks")),
    )
    op.create_table(
        "day_episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("checkin_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("effect", sa.Text(), nullable=True),
        sa.Column(
            "gave",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "took",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("choice", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
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
            "choice IN ('wanted', 'needed')", name=op.f("ck_day_episodes_choice_valid")
        ),
        sa.CheckConstraint(
            "effect IN ('gave', 'took', 'both', 'neutral')",
            name=op.f("ck_day_episodes_effect_valid"),
        ),
        sa.CheckConstraint(
            "gave <@ ARRAY['emotional', 'physical', 'social', 'cognitive']::text[]",
            name=op.f("ck_day_episodes_gave_valid"),
        ),
        sa.CheckConstraint(
            "kind IN ('close_people', 'work_social', 'crowd', 'work', 'chores', 'movement', 'hobby', 'leisure', 'solitude', 'rest', 'worries', 'decisions', 'self_demands')",
            name=op.f("ck_day_episodes_kind_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('button', 'llm', 'manual')", name=op.f("ck_day_episodes_source_valid")
        ),
        sa.CheckConstraint(
            "took <@ ARRAY['emotional', 'physical', 'social', 'cognitive']::text[]",
            name=op.f("ck_day_episodes_took_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["checkin_id"],
            ["checkins.id"],
            name=op.f("fk_day_episodes_checkin_id_checkins"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_day_episodes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_day_episodes")),
        sa.UniqueConstraint("checkin_id", "kind", name="uq_day_episodes_checkin_kind"),
    )
    op.create_index(
        "ix_day_episodes_user_date", "day_episodes", ["user_id", "local_date"], unique=False
    )
    op.add_column("checkins", sa.Column("obligations", sa.Text(), nullable=True))
    op.add_column("checkins", sa.Column("preoccupation", sa.Text(), nullable=True))
    op.add_column("checkins", sa.Column("preoccupation_note", sa.Text(), nullable=True))
    op.add_column("checkins", sa.Column("sleep_onset", sa.Text(), nullable=True))
    for column, values in CHECKIN_CHECKS.items():
        op.create_check_constraint(
            op.f(f"ck_checkins_{column}_valid"), "checkins", f"{column} IN ({values})"
        )


def downgrade() -> None:
    for column in CHECKIN_CHECKS:
        op.drop_constraint(op.f(f"ck_checkins_{column}_valid"), "checkins", type_="check")
    op.drop_column("checkins", "sleep_onset")
    op.drop_column("checkins", "preoccupation_note")
    op.drop_column("checkins", "preoccupation")
    op.drop_column("checkins", "obligations")
    op.drop_index("ix_day_episodes_user_date", table_name="day_episodes")
    op.drop_table("day_episodes")
    op.drop_table("checkin_marks")
