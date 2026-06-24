"""add expires_at to api_keys

Optional expiry for org API keys. NULL = never expires (current behavior, so this
migration is additive and changes no existing key). Enforced in
get_api_key_by_hash: a key past expires_at is treated as invalid.

Revision ID: d8b3f1a2c6e4
Revises: c4a7b1e0f9d2
Create Date: 2026-06-24 01:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b3f1a2c6e4"
down_revision: Union[str, None] = "c4a7b1e0f9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "expires_at")
