"""add extra column in workflow runs

Revision ID: efe356f488f9
Revises: 384be6596b36
Create Date: 2026-06-16 12:24:30.081058

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "efe356f488f9"
down_revision: Union[str, None] = "384be6596b36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "extra",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "extra")
