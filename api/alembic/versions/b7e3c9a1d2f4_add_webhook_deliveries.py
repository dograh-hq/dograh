"""add webhook_deliveries

Durable, retrying outbound webhook delivery so a transient network error can't
permanently drop a workflow's final webhook.

Revision ID: b7e3c9a1d2f4
Revises: 91cc6ba3e1c7
Create Date: 2026-06-28 19:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3c9a1d2f4"
down_revision: Union[str, None] = "91cc6ba3e1c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("delivery_uuid", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("webhook_name", sa.String(), nullable=True),
        sa.Column("endpoint_url", sa.String(), nullable=False),
        sa.Column(
            "http_method",
            sa.String(),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column("custom_headers", sa.JSON(), nullable=True),
        sa.Column("credential_uuid", sa.String(length=36), nullable=True),
        sa.Column("webhook_node_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "succeeded",
                "dead_letter",
                name="webhook_delivery_status",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "webhook_node_id",
            name="uq_webhook_deliveries_run_node",
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_delivery_uuid",
        "webhook_deliveries",
        ["delivery_uuid"],
        unique=True,
    )
    op.create_index(
        "idx_webhook_deliveries_run",
        "webhook_deliveries",
        ["workflow_run_id"],
        unique=False,
    )
    # Partial index for the sweeper's hot path: due pending deliveries.
    op.create_index(
        "idx_webhook_deliveries_pending_scheduled",
        "webhook_deliveries",
        ["scheduled_for"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_webhook_deliveries_pending_scheduled",
        table_name="webhook_deliveries",
    )
    op.drop_index("idx_webhook_deliveries_run", table_name="webhook_deliveries")
    op.drop_index(
        "ix_webhook_deliveries_delivery_uuid", table_name="webhook_deliveries"
    )
    op.drop_table("webhook_deliveries")
    op.execute("DROP TYPE IF EXISTS webhook_delivery_status")
