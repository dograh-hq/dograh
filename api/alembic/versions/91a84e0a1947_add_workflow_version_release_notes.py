"""Add extensible workflow definition metadata.

Revision ID: 91a84e0a1947
Revises: 3a7b91c5d402
Create Date: 2026-10-04 13:18:13.883753

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91a84e0a1947"
down_revision: Union[str, None] = "3a7b91c5d402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "extra_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_definitions", "extra_metadata")
