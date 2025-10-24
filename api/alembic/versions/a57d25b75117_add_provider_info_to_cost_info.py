"""add_provider_info_to_cost_info

Revision ID: a57d25b75117
Revises: 982ec8e434be
Create Date: 2025-10-21 12:28:06.053318

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from alembic_postgresql_enum import TableReference


# revision identifiers, used by Alembic.
revision: str = 'a57d25b75117'
down_revision: Union[str, None] = '982ec8e434be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add provider info to existing cost_info JSON for backward compatibility.
    This migration:
    1. Adds 'vonage' to workflow_run_mode enum
    2. Adds 'provider' field to cost_info for existing records
    3. Migrates TWILIO_CONFIGURATION key to TELEPHONY_CONFIGURATION
    """
    
    # Add 'vonage' to the workflow_run_mode enum using sync_enum_values like other migrations
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
    
    # Update workflow_runs to add provider info based on mode
    # Use jsonb_set() to add provider field while preserving existing data
    op.execute("""
        UPDATE workflow_runs 
        SET cost_info = jsonb_set(
            CASE 
                WHEN cost_info IS NULL OR cost_info::text = '{}' 
                THEN '{}'::jsonb 
                ELSE cost_info::jsonb 
            END,
            '{provider}', 
            '"twilio"'::jsonb,
            true
        )::json
        WHERE mode = 'twilio' 
          AND (cost_info IS NULL OR cost_info::text NOT LIKE '%provider%')
    """)
    
    op.execute("""
        UPDATE workflow_runs 
        SET cost_info = jsonb_set(
            CASE 
                WHEN cost_info IS NULL OR cost_info::text = '{}' 
                THEN '{}'::jsonb 
                ELSE cost_info::jsonb 
            END,
            '{provider}', 
            '"vonage"'::jsonb,
            true
        )::json
        WHERE mode = 'vonage' 
          AND (cost_info IS NULL OR cost_info::text NOT LIKE '%provider%')
    """)
    
    # Simply rename the key from TWILIO_CONFIGURATION to TELEPHONY_CONFIGURATION
    # Keep the same single-provider format
    op.execute("""
        UPDATE organization_configurations
        SET key = 'TELEPHONY_CONFIGURATION'
        WHERE key = 'TWILIO_CONFIGURATION';
    """)
    
    print("Migration complete: Added vonage to enum, provider info to cost_info, and renamed configuration key")


def downgrade() -> None:
    """
    Remove provider info and revert key name.
    Revert enum to previous state (removing 'vonage').
    """
    
    # Remove provider field from cost_info while preserving other data
    op.execute("""
        UPDATE workflow_runs 
        SET cost_info = (cost_info::jsonb - 'provider')::json
        WHERE cost_info::text LIKE '%provider%'
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
    
    print("Downgrade complete: Removed provider info and reverted key name")