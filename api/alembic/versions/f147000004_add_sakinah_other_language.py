"""preserve free-text scenario language for the Other selection

Revision ID: f147000004
Revises: e7f147000002
Create Date: 2026-09-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f147000004"
down_revision: str | None = "e7f147000002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sakinah_scenarios",
        sa.Column("other_language", sa.String(length=200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("sakinah_scenarios", "other_language")
