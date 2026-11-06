"""Durable builder requests, progress events, and atomic tool mutation receipts.

Revision ID: 93d7a618f2c4
Revises: 8b24d9a76c10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "93d7a618f2c4"
down_revision = "8b24d9a76c10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_builder_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "builder_run_session_idx",
        "workflow_builder_runs",
        ["organization_id", "user_id", "session_id", "created_at"],
    )
    op.create_index(
        "builder_run_active_idx",
        "workflow_builder_runs",
        ["organization_id", "user_id", "session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "workflow_builder_run_events",
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("workflow_builder_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_table(
        "tool_mutation_receipts",
        sa.Column("operation_id", sa.Text(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_table("tool_mutation_receipts")
    op.drop_table("workflow_builder_run_events")
    op.drop_table("workflow_builder_runs")
