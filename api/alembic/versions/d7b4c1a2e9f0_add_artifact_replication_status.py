"""add durable secondary artifact replication state

Revision ID: d7b4c1a2e9f0
Revises: c4a81f0e2b7d
Create Date: 2026-09-07 20:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d7b4c1a2e9f0"
down_revision: str | None = "c4a81f0e2b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_replication_status",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("primary_saved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("s3_saved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("primary_backend", sa.String(length=32), nullable=False),
        sa.Column("primary_bucket", sa.String(length=255), nullable=True),
        sa.Column("primary_object_key", sa.String(), nullable=False),
        sa.Column("s3_bucket", sa.String(length=255), nullable=True),
        sa.Column("s3_object_key", sa.String(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("s3_uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=128), nullable=True),
        sa.Column("replication_status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "artifact_type", "primary_object_key", name="uq_artifact_replication_run_type_primary_key"),
    )
    op.create_index("ix_artifact_replication_pending", "artifact_replication_status", ["replication_status", "s3_saved"])
    op.create_index("ix_artifact_replication_run_id", "artifact_replication_status", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_replication_run_id", table_name="artifact_replication_status")
    op.drop_index("ix_artifact_replication_pending", table_name="artifact_replication_status")
    op.drop_table("artifact_replication_status")
