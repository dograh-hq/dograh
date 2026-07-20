"""add last_validated_at on org config

Revision ID: c52dc3cccfb0
Revises: b7e3c9a1d2f4
Create Date: 2026-07-09 19:18:29.550267

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c52dc3cccfb0"
down_revision: Union[str, None] = "b7e3c9a1d2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organization_configurations",
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization_configurations", "last_validated_at")
