"""Add provider connections, named model configurations, and frozen run settings.

Revision ID: 4c8d92e6f503
Revises: 91a84e0a1947
"""

import sqlalchemy as sa
from alembic import op

revision = "4c8d92e6f503"
down_revision = "91a84e0a1947"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("provider_connections", "model_configurations"):
        columns = [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uuid", sa.String(36), nullable=False, unique=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        ]
        if name == "provider_connections":
            columns += [
                sa.Column("provider", sa.String(64), nullable=False),
                sa.Column("credentials", sa.JSON(), nullable=False),
                sa.Column("connection_settings", sa.JSON(), nullable=False),
                sa.Column("credential_version", sa.Integer(), nullable=False),
                sa.Column("credential_history", sa.JSON(), nullable=False),
                sa.Column("is_managed", sa.Boolean(), nullable=False),
            ]
        else:
            columns.append(sa.Column("configuration", sa.JSON(), nullable=False))
        op.create_table(name, *columns)
        op.create_index(f"ix_{name}_organization_id", name, ["organization_id"])
    op.add_column(
        "workflow_runs",
        sa.Column("model_configuration_overrides", sa.JSON(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("model_configuration_snapshot", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("workflow_runs", "model_configuration_snapshot")
    op.drop_column("workflow_runs", "model_configuration_overrides")
    op.drop_table("model_configurations")
    op.drop_table("provider_connections")
