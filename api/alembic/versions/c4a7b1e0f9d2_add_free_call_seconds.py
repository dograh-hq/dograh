"""add free_call_seconds_remaining to organizations

Trial minute ledger: new orgs are granted a fixed number of free outbound call
seconds (default 1800 = 30 min). NULL means UNLIMITED (existing orgs, and any
paid/owner org), so this migration is additive and leaves every current org
unmetered. Decremented per completed call; gated at campaign start/resume,
per dispatch batch, and the public trigger.

Revision ID: c4a7b1e0f9d2
Revises: a7f3c1e9b2d6
Create Date: 2026-06-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a7b1e0f9d2"
down_revision: Union[str, None] = "a7f3c1e9b2d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no default: existing orgs stay NULL = unlimited (instant, no rewrite).
    op.add_column(
        "organizations",
        sa.Column("free_call_seconds_remaining", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "free_call_seconds_remaining")
