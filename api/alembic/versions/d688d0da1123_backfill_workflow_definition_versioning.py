"""backfill workflow definition versioning

Copy workflow_configurations, template_context_variables, call_disposition_codes
from the workflows table into the is_current=True definition for each workflow.
Set that definition as status='published', version_number=1.
Set all other definitions to status='archived'.
Point workflows.released_definition_id to the published definition.

Revision ID: d688d0da1123
Revises: a399b39479fe
Create Date: 2026-04-07 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d688d0da1123"
down_revision: Union[str, None] = "a399b39479fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: For each workflow's is_current=True definition, copy configs from
    # the workflow table and mark as published with version_number=1.
    conn.execute(
        sa.text("""
        UPDATE workflow_definitions wd
        SET
            workflow_configurations = w.workflow_configurations,
            template_context_variables = w.template_context_variables,
            status = 'published',
            version_number = 1,
            published_at = wd.created_at
        FROM workflows w
        WHERE wd.workflow_id = w.id
          AND wd.is_current = true
    """)
    )

    # Step 2: Mark all pre-versioning non-current definitions as legacy.
    conn.execute(
        sa.text("""
        UPDATE workflow_definitions
        SET status = 'legacy'
        WHERE is_current = false
    """)
    )

    # Step 3: Set released_definition_id on workflows to their published definition.
    conn.execute(
        sa.text("""
        UPDATE workflows w
        SET released_definition_id = wd.id
        FROM workflow_definitions wd
        WHERE wd.workflow_id = w.id
          AND wd.is_current = true
    """)
    )


def downgrade() -> None:
    conn = op.get_bind()

    # Clear the released pointer
    conn.execute(
        sa.text("""
        UPDATE workflows SET released_definition_id = NULL
    """)
    )

    # Reset all definitions back to server defaults
    conn.execute(
        sa.text("""
        UPDATE workflow_definitions
        SET
            status = 'published',
            version_number = NULL,
            published_at = NULL,
            workflow_configurations = '{}',
            template_context_variables = '{}'
    """)
    )
