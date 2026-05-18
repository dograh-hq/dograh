"""add workflow_uuid

Revision ID: cdcf9f65913b
Revises: a1b2c3d4e5f6
Create Date: 2026-04-25 18:24:45.954049

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cdcf9f65913b"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column as nullable so existing rows are accepted.
    op.add_column(
        "workflows",
        sa.Column("workflow_uuid", sa.String(length=36), nullable=True),
    )

    # 2. Backfill UUIDs for existing rows. gen_random_uuid() is built-in on
    #    PostgreSQL 13+; cast to text to match the String(36) column type.
    op.execute(
        "UPDATE workflows SET workflow_uuid = gen_random_uuid()::text "
        "WHERE workflow_uuid IS NULL"
    )

    # 3. Now that every row has a value, enforce NOT NULL.
    op.alter_column("workflows", "workflow_uuid", nullable=False)

    # 4. Create the unique index.
    op.create_index(
        op.f("ix_workflows_workflow_uuid"),
        "workflows",
        ["workflow_uuid"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflows_workflow_uuid"), table_name="workflows")
    op.drop_column("workflows", "workflow_uuid")
