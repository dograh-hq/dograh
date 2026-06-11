"""add voicelink provisioning columns to organizations

Revision ID: c9e2f5a17d04
Revises: 6bd9f67ec994
Create Date: 2026-06-11 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e2f5a17d04"
down_revision: Union[str, None] = "6bd9f67ec994"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("voicelink_client_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("voicelink_username", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("voicelink_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("voicelink_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "voicelink_error")
    op.drop_column("organizations", "voicelink_status")
    op.drop_column("organizations", "voicelink_username")
    op.drop_column("organizations", "voicelink_client_id")
