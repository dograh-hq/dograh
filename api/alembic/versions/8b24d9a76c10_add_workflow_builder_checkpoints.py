"""Own the workflow builder checkpoint schema in Alembic.

Uses the layout of langgraph-checkpoint-postgres 3.1.2 (schema version 9).
IF NOT EXISTS also adopts tables created by the former builder setup script,
preserving conversations. The compatibility ledger is retained for existing
installations; all subsequent schema changes must be explicit Alembic revisions.

Revision ID: 8b24d9a76c10
Revises: 91a84e0a1947
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "8b24d9a76c10"
down_revision = "91a84e0a1947"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), primary_key=True, autoincrement=False),
        if_not_exists=True,
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
        if_not_exists=True,
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False, server_default=""),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary()),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "channel", "version"),
        if_not_exists=True,
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("checkpoint_ns", sa.Text(), nullable=False, server_default=""),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"
        ),
        if_not_exists=True,
    )
    # Complete any partially applied setup from the former upstream migration path.
    op.alter_column("checkpoint_blobs", "blob", nullable=True)
    op.execute(
        "ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT ''"
    )
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        op.create_index(
            f"{table}_thread_id_idx", table, ["thread_id"], if_not_exists=True
        )
    op.execute(
        "INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, 9) ON CONFLICT DO NOTHING"
    )


def downgrade():
    for table in (
        "checkpoint_writes",
        "checkpoint_blobs",
        "checkpoints",
        "checkpoint_migrations",
    ):
        op.drop_table(table)
