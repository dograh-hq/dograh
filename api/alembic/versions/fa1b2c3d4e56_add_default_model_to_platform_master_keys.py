"""add default_model to platform_master_keys

Revision ID: fa1b2c3d4e56
Revises: e9a1b2c3d4e5
Create Date: 2026-09-12 21:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fa1b2c3d4e56"
down_revision: Union[str, None] = "e9a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE platform_master_keys ADD COLUMN IF NOT EXISTS default_model VARCHAR(128)"))


def downgrade() -> None:
    op.drop_column("platform_master_keys", "default_model")
