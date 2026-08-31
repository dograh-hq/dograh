"""add telephony configuration inactive flag

Revision ID: c7a1e4f93b26
Revises: b41f7c9d2e05
Create Date: 2026-08-06 09:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a1e4f93b26"
down_revision: Union[str, None] = "b41f7c9d2e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telephony_configurations",
        sa.Column(
            "inactive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "telephony_configurations",
        sa.Column("inactive_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telephony_configurations",
        sa.Column("inactive_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telephony_configurations", "inactive_reason")
    op.drop_column("telephony_configurations", "inactive_since")
    op.drop_column("telephony_configurations", "inactive")
