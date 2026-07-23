"""add wait to tool category

Revision ID: ceeaf3c37b2f
Revises: gg11dd223344
Create Date: 2026-07-22 12:42:18.521312

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = 'ceeaf3c37b2f'
down_revision: Union[str, None] = '00b0201ad918'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=[
            "http_api",
            "end_call",
            "transfer_call",
            "calculator",
            "native",
            "integration",
            "mcp",
            "wait",
        ],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="tools", column_name="category"
            )
        ],
        enum_values_to_rename=[],
    )

def downgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="tool_category",
        new_values=[
            "http_api",
            "end_call",
            "transfer_call",
            "calculator",
            "native",
            "integration",
            "mcp",
        ],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="tools", column_name="category"
            )
        ],
        enum_values_to_rename=[],
    )
