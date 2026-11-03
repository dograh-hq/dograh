"""Add campaign claim timestamp.

Revision ID: 6d7b40a9c2e1
Revises: c4e21b7f80a9
"""

import sqlalchemy as sa
from alembic import op

revision = "6d7b40a9c2e1"
down_revision = "c4e21b7f80a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Recovery gives existing processing rows a full lease on first observation.
    # Their source creation time cannot tell us when a worker claimed them.
    op.add_column(
        "queued_runs",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("queued_runs", "claimed_at")
