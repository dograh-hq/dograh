"""add_state_field_to_workflow_runs

Revision ID: 49a8fe6841e6
Revises: a188ff90e76f
Create Date: 2025-12-10 17:34:31.232048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '49a8fe6841e6'
down_revision: Union[str, None] = 'a188ff90e76f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the workflow_run_state enum type
    workflow_run_state_enum = sa.Enum(
        'initialized', 'running', 'completed',
        name='workflow_run_state'
    )
    workflow_run_state_enum.create(op.get_bind())
    
    # Add the state column to workflow_runs table (nullable first)
    op.add_column(
        'workflow_runs',
        sa.Column(
            'state',
            sa.Enum('initialized', 'running', 'completed', name='workflow_run_state'),
            nullable=True
        )
    )
    
    # Set appropriate state values for existing records
    # Completed workflows should be marked as 'completed'
    # Non-completed workflows should be marked as 'initialized'
    op.execute("""
        UPDATE workflow_runs 
        SET state = CASE 
            WHEN is_completed = true THEN 'completed'::workflow_run_state
            ELSE 'initialized'::workflow_run_state
        END
    """)
    
    # Now make the column non-nullable with 'initialized' as default for new records
    op.alter_column(
        'workflow_runs',
        'state',
        nullable=False,
        server_default='initialized'
    )


def downgrade() -> None:
    # Drop the state column
    op.drop_column('workflow_runs', 'state')
    
    # Drop the enum type
    sa.Enum(name='workflow_run_state').drop(op.get_bind())
