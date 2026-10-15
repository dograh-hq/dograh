"""Add whatsapp tool category.

Revision ID: e1f2a3b4c5d6
Revises: c82d4f6a9b10
"""

from alembic import op
from alembic_postgresql_enum import TableReference

revision = "e1f2a3b4c5d6"
down_revision = "c82d4f6a9b10"
branch_labels = None
depends_on = None


def _sync(values):
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=values,
        affected_columns=[
            TableReference(
                table_schema="public", table_name="tools", column_name="category"
            )
        ],
        enum_values_to_rename=[],
    )


def upgrade():
    _sync(
        [
            "http_api",
            "end_call",
            "transfer_call",
            "transfer_agent",
            "calculator",
            "native",
            "integration",
            "mcp",
            "whatsapp",
        ]
    )


def downgrade():
    _sync(
        [
            "http_api",
            "end_call",
            "transfer_call",
            "transfer_agent",
            "calculator",
            "native",
            "integration",
            "mcp",
        ]
    )
