"""add key to user_configurations

Turns user_configurations into a per-user keyed JSON store mirroring
organization_configurations. Existing rows (the legacy v1 AI model
configuration blob) are backfilled with key MODEL_CONFIGURATION.

Revision ID: 91cc6ba3e1c7
Revises: efe356f488f9
Create Date: 2026-06-12 21:04:25.561529

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91cc6ba3e1c7"
down_revision: Union[str, None] = "efe356f488f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill existing rows (all legacy model-config blobs) via the server
    # default, then drop the default — application code always supplies key.
    op.add_column(
        "user_configurations",
        sa.Column(
            "key",
            sa.String(),
            nullable=False,
            server_default="MODEL_CONFIGURATION",
        ),
    )

    op.create_unique_constraint(
        "_user_configuration_key_uc", "user_configurations", ["user_id", "key"]
    )
    op.alter_column("user_configurations", "key", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "_user_configuration_key_uc", "user_configurations", type_="unique"
    )
    # Non-model-config rows (e.g. ONBOARDING) have no meaning in the old
    # single-blob schema; the old code would read them as the user's model
    # config, so they must not survive the downgrade.
    op.execute("DELETE FROM user_configurations WHERE key != 'MODEL_CONFIGURATION'")
    op.drop_column("user_configurations", "key")
