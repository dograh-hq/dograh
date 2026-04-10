"""unique recording id per org and workflow

Revision ID: 67a5cf3e09d0
Revises: e7254d2c6c18
Create Date: 2026-04-09 17:03:38.302041

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "67a5cf3e09d0"
down_revision: Union[str, None] = "e7254d2c6c18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Widen column from 16 to 64 chars for descriptive names
    op.alter_column(
        "workflow_recordings",
        "recording_id",
        existing_type=sa.VARCHAR(length=16),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    # Drop the old globally-unique index
    op.drop_index(
        op.f("ix_workflow_recordings_recording_id"), table_name="workflow_recordings"
    )
    # Re-create as non-unique index for lookups
    op.create_index(
        "ix_workflow_recordings_recording_id",
        "workflow_recordings",
        ["recording_id"],
        unique=False,
    )
    # Add composite unique constraint (recording_id, organization_id, workflow_id)
    op.create_unique_constraint(
        "uq_workflow_recordings_recording_id_org_wf",
        "workflow_recordings",
        ["recording_id", "organization_id", "workflow_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workflow_recordings_recording_id_org_wf",
        "workflow_recordings",
        type_="unique",
    )
    op.drop_index(
        "ix_workflow_recordings_recording_id", table_name="workflow_recordings"
    )
    op.create_index(
        op.f("ix_workflow_recordings_recording_id"),
        "workflow_recordings",
        ["recording_id"],
        unique=True,
    )
    op.alter_column(
        "workflow_recordings",
        "recording_id",
        existing_type=sa.String(length=64),
        type_=sa.VARCHAR(length=16),
        existing_nullable=False,
    )
