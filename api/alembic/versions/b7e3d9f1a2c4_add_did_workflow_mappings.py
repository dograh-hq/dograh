"""add did workflow mappings

Revision ID: b7e3d9f1a2c4
Revises: 6fd8fac02883
Create Date: 2026-02-25 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3d9f1a2c4"
down_revision: Union[str, None] = "6fd8fac02883"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "did_workflow_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("did_number", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "did_number", name="_org_did_uc"
        ),
    )
    op.create_index(
        op.f("ix_did_workflow_mappings_id"),
        "did_workflow_mappings",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_did_workflow_mappings_organization_id"),
        "did_workflow_mappings",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_did_workflow_mappings_organization_id"),
        table_name="did_workflow_mappings",
    )
    op.drop_index(
        op.f("ix_did_workflow_mappings_id"),
        table_name="did_workflow_mappings",
    )
    op.drop_table("did_workflow_mappings")
