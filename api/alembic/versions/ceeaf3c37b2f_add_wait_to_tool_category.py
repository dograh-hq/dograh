"""add wait to tool category

Revision ID: ceeaf3c37b2f
Revises: gg11dd223344
Create Date: 2026-07-22 12:42:18.521312

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ceeaf3c37b2f'
down_revision: Union[str, None] = 'gg11dd223344'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tool_category ADD VALUE IF NOT EXISTS 'wait'")

def downgrade() -> None:
    pass
