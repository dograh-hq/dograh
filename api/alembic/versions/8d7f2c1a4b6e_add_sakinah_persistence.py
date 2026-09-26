"""add authenticated Sakinah scenarios and run summaries

Revision ID: 8d7f2c1a4b6e
Revises: c7a1e4f93b26
Create Date: 2026-08-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8d7f2c1a4b6e"
down_revision: Union[str, None] = "c7a1e4f93b26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sakinah_scenarios",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("age", sa.String(), nullable=False),
        sa.Column("gender", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("emotion", sa.String(), nullable=False),
        sa.Column("communication_style", sa.Text(), nullable=False),
        sa.Column("initial_information", sa.Text(), nullable=False),
        sa.Column("hidden_information", sa.Text(), nullable=False),
        sa.Column("disclosure", sa.Text(), nullable=False),
        sa.Column("behaviour", sa.Text(), nullable=False),
        sa.Column("background", sa.Text(), nullable=False),
        sa.Column("additional_factors", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("freestyle_prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sakinah_scenarios_user_id", "sakinah_scenarios", ["user_id"], unique=False
    )
    op.create_index(
        "ix_sakinah_scenarios_user_sequence",
        "sakinah_scenarios",
        ["user_id", "sequence"],
        unique=False,
    )

    op.create_table(
        "sakinah_runs",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("service_user_agent_id", sa.Integer(), nullable=True),
        sa.Column("service_user_run_id", sa.Integer(), nullable=True),
        sa.Column("scenario", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("experiment_mode", sa.String(length=64), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("transcript_url", sa.String(), nullable=True),
        sa.Column("conversation", sa.JSON(), nullable=False),
        sa.Column("preview_data", sa.JSON(), nullable=False),
        sa.Column("recording_url", sa.String(), nullable=True),
        sa.Column("recording_file_reference", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calm_turns", sa.JSON(), nullable=False),
        sa.Column("timings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_sakinah_runs_user_id", "sakinah_runs", ["user_id"], unique=False)
    op.create_index("ix_sakinah_runs_run_id", "sakinah_runs", ["run_id"], unique=False)
    op.create_index(
        "ix_sakinah_runs_user_started_at",
        "sakinah_runs",
        ["user_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sakinah_runs_user_started_at", table_name="sakinah_runs")
    op.drop_index("ix_sakinah_runs_run_id", table_name="sakinah_runs")
    op.drop_index("ix_sakinah_runs_user_id", table_name="sakinah_runs")
    op.drop_table("sakinah_runs")
    op.drop_index("ix_sakinah_scenarios_user_sequence", table_name="sakinah_scenarios")
    op.drop_index("ix_sakinah_scenarios_user_id", table_name="sakinah_scenarios")
    op.drop_table("sakinah_scenarios")
