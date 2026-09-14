"""add platform master keys and inventory

Revision ID: e9a1b2c3d4e5
Revises: f3a1c47b9e02
Create Date: 2026-09-10 17:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e9a1b2c3d4e5"
down_revision: Union[str, None] = "f3a1c47b9e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create platform_master_keys table
    op.create_table(
        "platform_master_keys",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("service_type", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key", sa.String(length=512), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("models_pricing", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_platform_master_keys_type_provider",
        "platform_master_keys",
        ["service_type", "provider"],
    )
    op.create_index(
        "ix_platform_master_keys_default",
        "platform_master_keys",
        ["service_type", "is_default"],
    )

    # 2. Add wallet_balance_usd to organizations
    op.add_column(
        "organizations",
        sa.Column(
            "wallet_balance_usd",
            sa.Float(),
            nullable=False,
            server_default=sa.text("10.0"),
        ),
    )

    # 3. Add is_platform_inventory to telephony_configurations
    op.add_column(
        "telephony_configurations",
        sa.Column(
            "is_platform_inventory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 4. Add inventory and assignment fields to telephony_phone_numbers
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "is_platform_inventory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "pool_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'dedicated'"),
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "monthly_price_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "telephony_phone_numbers",
        sa.Column(
            "assigned_organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_telephony_phone_numbers_assigned_org",
        "telephony_phone_numbers",
        ["assigned_organization_id"],
    )

    # 5. Add price_per_second to workflows
    op.add_column(
        "workflows",
        sa.Column(
            "price_per_second",
            sa.Float(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflows", "price_per_second")
    op.drop_index("ix_telephony_phone_numbers_assigned_org", table_name="telephony_phone_numbers")
    op.drop_column("telephony_phone_numbers", "assigned_organization_id")
    op.drop_column("telephony_phone_numbers", "monthly_price_cents")
    op.drop_column("telephony_phone_numbers", "pool_type")
    op.drop_column("telephony_phone_numbers", "is_platform_inventory")
    op.drop_column("telephony_configurations", "is_platform_inventory")
    op.drop_column("organizations", "wallet_balance_usd")
    op.drop_index("ix_platform_master_keys_default", table_name="platform_master_keys")
    op.drop_index("ix_platform_master_keys_type_provider", table_name="platform_master_keys")
    op.drop_table("platform_master_keys")
