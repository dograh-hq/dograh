"""Add sms_conversations + sms_inbound_messages (Twilio SMS inbound channel).

Revision ID: c2d8e4f6a1b9
Revises: d6a83e2b19f0
Create Date: 2026-10-05 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d8e4f6a1b9"
down_revision = "d6a83e2b19f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sms_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("workflows.id"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sms_conversations_expires_at", "sms_conversations", ["expires_at"]
    )
    op.create_index(
        "ix_sms_conversations_phone_to_number",
        "sms_conversations",
        ["phone", "to_number"],
    )

    op.create_table(
        "sms_inbound_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_sid", sa.String(length=64), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("sms_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "workflow_run_id",
            sa.Integer(),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_number", sa.String(length=32), nullable=True),
        sa.Column("to_number", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="processing",
        ),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sms_inbound_messages_message_sid",
        "sms_inbound_messages",
        ["message_sid"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_sms_inbound_messages_message_sid", table_name="sms_inbound_messages")
    op.drop_table("sms_inbound_messages")
    op.drop_index("ix_sms_conversations_phone_to_number", table_name="sms_conversations")
    op.drop_index("ix_sms_conversations_expires_at", table_name="sms_conversations")
    op.drop_table("sms_conversations")
