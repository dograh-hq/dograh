"""add enable_dtmf

Revision ID: gg11dd223344
Revises: fefdd1835b7d
Create Date: 2026-07-20 12:26:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "gg11dd223344"
down_revision: Union[str, None] = "fefdd1835b7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("enable_dtmf", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("workflows", "enable_dtmf")
