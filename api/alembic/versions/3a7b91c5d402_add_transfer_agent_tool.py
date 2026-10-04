"""Add saved-agent transfer tool category.

Revision ID: 3a7b91c5d402
Revises: 6d7b40a9c2e1
"""

from alembic import op
from alembic_postgresql_enum import TableReference

revision = "3a7b91c5d402"
down_revision = "6d7b40a9c2e1"
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
        ]
    )


def downgrade():
    # Refuse removal while saved tools still use this category.
    _sync(
        [
            "http_api",
            "end_call",
            "transfer_call",
            "calculator",
            "native",
            "integration",
            "mcp",
        ]
    )
