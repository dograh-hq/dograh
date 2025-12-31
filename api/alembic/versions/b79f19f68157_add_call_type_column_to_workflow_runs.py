"""add call_type column to workflow_runs

Revision ID: b79f19f68157
Revises: 488eb58e4e6e
Create Date: 2026-01-08 21:20:17.298334

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b79f19f68157'
down_revision: Union[str, None] = '488eb58e4e6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the workflow_call_type enum
    sa.Enum('inbound', 'outbound', name='workflow_call_type').create(op.get_bind())
    
    # Add call_type column to workflow_runs table
    op.add_column('workflow_runs', sa.Column('call_type', 
                                           postgresql.ENUM('inbound', 'outbound', name='workflow_call_type', create_type=False), 
                                           server_default=sa.text("'outbound'::workflow_call_type"), 
                                           nullable=False))


def downgrade() -> None:
    # Drop the call_type column
    op.drop_column('workflow_runs', 'call_type')
    
    # Drop the workflow_call_type enum
    sa.Enum('inbound', 'outbound', name='workflow_call_type').drop(op.get_bind())
