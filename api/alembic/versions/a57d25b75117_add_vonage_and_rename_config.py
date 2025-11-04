"""add_vonage_and_rename_config

Revision ID: a57d25b75117
Revises: 982ec8e434be
Create Date: 2025-10-21 12:28:06.053318

"""
from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference


# revision identifiers, used by Alembic.
revision: str = 'a57d25b75117'
down_revision: Union[str, None] = '982ec8e434be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add vonage support and rename configuration keys.
    This migration:
    1. Adds 'vonage' to workflow_run_mode enum
    2. Migrates TWILIO_CONFIGURATION key to TELEPHONY_CONFIGURATION
    3. Renames twilio_status_callbacks to telephony_status_callbacks in workflow_run logs
    """
    
    # Add 'vonage' to the workflow_run_mode enum
    op.sync_enum_values(
        enum_schema="public",
        enum_name="workflow_run_mode",
        new_values=["twilio", "stasis", "webrtc", "smallwebrtc", "VOICE", "CHAT", "vonage"],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="workflow_runs", column_name="mode"
            )
        ],
        enum_values_to_rename=[],
    )
    
    # Rename the key from TWILIO_CONFIGURATION to TELEPHONY_CONFIGURATION
    op.execute("""
        UPDATE organization_configurations
        SET key = 'TELEPHONY_CONFIGURATION'
        WHERE key = 'TWILIO_CONFIGURATION';
    """)
    
    # Rename twilio_status_callbacks to telephony_status_callbacks in workflow_run logs
    op.execute("""
        UPDATE workflow_runs
        SET logs = jsonb_set(
            logs::jsonb - 'twilio_status_callbacks',
            '{telephony_status_callbacks}',
            COALESCE(logs::jsonb->'twilio_status_callbacks', '[]'::jsonb)
        )
        WHERE logs::jsonb ? 'twilio_status_callbacks';
    """)
    
    print("Migration complete: Added vonage to enum, renamed configuration key, and updated status callback keys")


def downgrade() -> None:
    """
    Revert configuration key names and enum.
    """
    
    # Revert telephony_status_callbacks to twilio_status_callbacks in workflow_run logs
    op.execute("""
        UPDATE workflow_runs
        SET logs = jsonb_set(
            logs::jsonb - 'telephony_status_callbacks',
            '{twilio_status_callbacks}',
            COALESCE(logs::jsonb->'telephony_status_callbacks', '[]'::jsonb)
        )
        WHERE logs::jsonb ? 'telephony_status_callbacks';
    """)
    
    # Revert key name
    op.execute("""
        UPDATE organization_configurations
        SET key = 'TWILIO_CONFIGURATION'
        WHERE key = 'TELEPHONY_CONFIGURATION';
    """)
    
    # Revert enum to previous state
    op.sync_enum_values(
        enum_schema="public",
        enum_name="workflow_run_mode",
        new_values=["twilio", "stasis", "webrtc", "smallwebrtc", "VOICE", "CHAT"],
        affected_columns=[
            TableReference(
                table_schema="public", table_name="workflow_runs", column_name="mode"
            )
        ],
        enum_values_to_rename=[],
    )
    
    print("Downgrade complete: Reverted configuration key names and enum")