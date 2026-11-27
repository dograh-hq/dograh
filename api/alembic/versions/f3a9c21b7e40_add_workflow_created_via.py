"""Add workflows.created_via for agent-builder analytics attribution.

Immutable creation-provenance column ("builder" | "template" | "blank"),
stamped at creation. Pre-existing rows stay NULL; ``WorkflowClient.
get_workflow_origin`` resolves NULL to a best-effort value at read time (a
builder_sessions link implies builder origin, otherwise "unknown"), so no
backfill is needed for the analytics path.

Revision ID: f3a9c21b7e40
Revises: 93d7a618f2c4
"""

import sqlalchemy as sa
from alembic import op

revision = "f3a9c21b7e40"
down_revision = "93d7a618f2c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflows",
        sa.Column("created_via", sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column("workflows", "created_via")
