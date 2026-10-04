"""merge Sakinah persistence and Dograh v1.47 migration heads

Revision ID: e7f147000001
Revises: 3a7b91c5d402, 4f1c2d3e5a67
Create Date: 2026-09-26 00:00:00.000000

This is an Alembic graph merge only: each parent branch owns separate schema
changes.  A no-op merge lets an existing Sakinah installation receive all
Dograh v1.47 migrations, and lets a fresh v1.47 installation receive the
Sakinah persistence tables, without asking operators to stamp revisions.
"""

from typing import Sequence, Union


revision: str = "e7f147000001"
down_revision: Union[str, Sequence[str], None] = (
    "3a7b91c5d402",
    "4f1c2d3e5a67",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the independently-applied migration branches."""


def downgrade() -> None:
    """Split history only; parent migrations retain their own downgrades."""
