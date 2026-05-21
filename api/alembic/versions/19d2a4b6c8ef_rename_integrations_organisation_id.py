"""rename integrations organisation_id to organization_id

Revision ID: 19d2a4b6c8ef
Revises: 0a1b2c3d4e5f

Create Date: 2026-05-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "19d2a4b6c8ef"
down_revision: Union[str, None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "integrations",
        "organisation_id",
        new_column_name="organization_id",
    )


def downgrade() -> None:
    op.alter_column(
        "integrations",
        "organization_id",
        new_column_name="organisation_id",
    )
