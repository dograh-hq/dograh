"""Remove provider origin metadata and credential version storage.

Revision ID: a7128e93bc40
Revises: 4c8d92e6f503

Downgrade restores the columns with defaults, not discarded credential history.
Current credentials and all connection/configuration UUIDs are preserved.
"""

import sqlalchemy as sa
from alembic import op

revision = "a7128e93bc40"
down_revision = "4c8d92e6f503"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("provider_connections", "is_managed")
    op.drop_column("provider_connections", "credential_history")
    op.drop_column("provider_connections", "credential_version")


def downgrade():
    for name, column_type, default in (
        ("is_managed", sa.Boolean(), sa.false()),
        ("credential_history", sa.JSON(), sa.text("'{}'::json")),
        ("credential_version", sa.Integer(), sa.text("1")),
    ):
        op.add_column(
            "provider_connections",
            sa.Column(name, column_type, nullable=False, server_default=default),
        )
