"""add voicelink_provision_secret to organizations

Stores a Fernet-encrypted copy of the signup password while an org is not yet
provisioned, so admin "Create client" can reuse the same platform password.
Wiped on successful provisioning.

Revision ID: a7f3c1e9b2d6
Revises: c9e2f5a17d04
Create Date: 2026-06-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f3c1e9b2d6"
down_revision: Union[str, None] = "c9e2f5a17d04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("voicelink_provision_secret", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "voicelink_provision_secret")
