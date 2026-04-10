"""make tts columns nullable on workflow_recordings

Revision ID: a1b2c3d4e5f6
Revises: 67a5cf3e09d0
Create Date: 2026-04-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str = "67a5cf3e09d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "workflow_recordings", "tts_provider", existing_type=sa.String(), nullable=True
    )
    op.alter_column(
        "workflow_recordings", "tts_model", existing_type=sa.String(), nullable=True
    )
    op.alter_column(
        "workflow_recordings", "tts_voice_id", existing_type=sa.String(), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "workflow_recordings", "tts_voice_id", existing_type=sa.String(), nullable=False
    )
    op.alter_column(
        "workflow_recordings", "tts_model", existing_type=sa.String(), nullable=False
    )
    op.alter_column(
        "workflow_recordings", "tts_provider", existing_type=sa.String(), nullable=False
    )
