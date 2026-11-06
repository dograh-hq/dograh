"""Index unfinished post-call processing for recovery.

Revision ID: d6a83e2b19f0
Revises: c82d4f6a9b10
"""

import sqlalchemy as sa
from alembic import op

revision = "d6a83e2b19f0"
down_revision = "c82d4f6a9b10"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        # An interrupted concurrent build can leave an invalid index behind.
        # Rebuild on retry rather than treating its existence as success.
        op.drop_index(
            "idx_workflow_runs_pending_completion",
            table_name="workflow_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "idx_workflow_runs_pending_completion",
            "workflow_runs",
            ["id"],
            postgresql_where=sa.text(
                "is_completed IS TRUE AND (extra -> 'workflow_completion' ->> 'status') IN ('pending', 'running')"
            ),
            postgresql_concurrently=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_workflow_runs_pending_completion",
            table_name="workflow_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
