"""add plivo mode

Revision ID: f2e1d0c9b8a7
Revises: a1b2c3d4e5f6, 67a5cf3e09d0
Create Date: 2026-04-13 16:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "f2e1d0c9b8a7"
down_revision: Union[str, Sequence[str], None] = (
    "a1b2c3d4e5f6",
    "67a5cf3e09d0",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="workflow_run_mode",
        new_values=[
            "ari",
            "plivo",
            "twilio",
            "vonage",
            "vobiz",
            "cloudonix",
            "telnyx",
            "webrtc",
            "smallwebrtc",
            "stasis",
            "VOICE",
            "CHAT",
        ],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="workflow_runs", column_name="mode"
            )
        ],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    op.sync_enum_values(
        enum_schema="public",
        enum_name="workflow_run_mode",
        new_values=[
            "ari",
            "twilio",
            "vonage",
            "vobiz",
            "cloudonix",
            "telnyx",
            "webrtc",
            "smallwebrtc",
            "stasis",
            "VOICE",
            "CHAT",
        ],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="workflow_runs", column_name="mode"
            )
        ],
        enum_values_to_rename=[],
    )
