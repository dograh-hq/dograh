"""make recordings org-scoped instead of workflow-scoped

Revision ID: 67a5cf3e09d0
Revises: 3cd3155084a2
Create Date: 2026-04-09 17:03:38.302041

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "67a5cf3e09d0"
down_revision: Union[str, None] = "3cd3155084a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Widen recording_id from 16 to 64 chars for descriptive names
    op.alter_column(
        "workflow_recordings",
        "recording_id",
        existing_type=sa.VARCHAR(length=16),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    # 2. Make workflow_id nullable — recordings are now org-scoped
    op.alter_column(
        "workflow_recordings",
        "workflow_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # 3. Drop the old globally-unique index on recording_id
    op.drop_index(
        "ix_workflow_recordings_recording_id",
        table_name="workflow_recordings",
    )

    # 4. Re-create as non-unique index (for lookups)
    op.create_index(
        "ix_workflow_recordings_recording_id",
        "workflow_recordings",
        ["recording_id"],
        unique=False,
    )

    # 5. Add unique constraint (recording_id, organization_id)
    op.create_unique_constraint(
        "uq_workflow_recordings_recording_id_org",
        "workflow_recordings",
        ["recording_id", "organization_id"],
    )

    # 6. Drop the workflow+TTS scope index (no longer relevant)
    op.drop_index(
        "ix_workflow_recordings_tts_scope",
        table_name="workflow_recordings",
    )


def downgrade() -> None:
    # Re-create the TTS scope index
    op.create_index(
        "ix_workflow_recordings_tts_scope",
        "workflow_recordings",
        ["workflow_id", "tts_provider", "tts_model", "tts_voice_id"],
    )

    # Drop the org-scoped unique constraint
    op.drop_constraint(
        "uq_workflow_recordings_recording_id_org",
        "workflow_recordings",
        type_="unique",
    )

    # Drop non-unique index and re-create as unique
    op.drop_index(
        "ix_workflow_recordings_recording_id",
        table_name="workflow_recordings",
    )
    op.create_index(
        "ix_workflow_recordings_recording_id",
        "workflow_recordings",
        ["recording_id"],
        unique=True,
    )

    # Make workflow_id NOT NULL again
    op.alter_column(
        "workflow_recordings",
        "workflow_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    # Revert recording_id width
    op.alter_column(
        "workflow_recordings",
        "recording_id",
        existing_type=sa.String(length=64),
        type_=sa.VARCHAR(length=16),
        existing_nullable=False,
    )
