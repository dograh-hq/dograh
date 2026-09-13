"""add whatsapp_call_permissions table

Revision ID: e1a2b3c4d5e6
Revises: f3a1c47b9e02
Create Date: 2026-09-10 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5e6"
down_revision: Union[str, None] = "f3a1c47b9e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_call_permissions",
        sa.Column(
            "id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("telephony_configuration_id", sa.Integer(), nullable=False),
        sa.Column("phone_number_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_phone_number", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="pending"
        ),
        sa.Column("permission_type", sa.String(length=32), nullable=True),
        sa.Column("meta_message_id", sa.String(length=128), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telephony_configuration_id"],
            ["telephony_configurations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "telephony_configuration_id",
            "recipient_phone_number",
            name="uq_whatsapp_perm_config_recipient",
        ),
    )
    op.create_index(
        "ix_whatsapp_call_permissions_id",
        "whatsapp_call_permissions",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_call_permissions_organization_id",
        "whatsapp_call_permissions",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_call_permissions_telephony_configuration_id",
        "whatsapp_call_permissions",
        ["telephony_configuration_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_call_permissions_phone_number_id",
        "whatsapp_call_permissions",
        ["phone_number_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_call_permissions_recipient_phone_number",
        "whatsapp_call_permissions",
        ["recipient_phone_number"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_perm_lookup",
        "whatsapp_call_permissions",
        ["phone_number_id", "recipient_phone_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_whatsapp_perm_lookup", table_name="whatsapp_call_permissions")
    op.drop_index(
        "ix_whatsapp_call_permissions_recipient_phone_number",
        table_name="whatsapp_call_permissions",
    )
    op.drop_index(
        "ix_whatsapp_call_permissions_phone_number_id",
        table_name="whatsapp_call_permissions",
    )
    op.drop_index(
        "ix_whatsapp_call_permissions_telephony_configuration_id",
        table_name="whatsapp_call_permissions",
    )
    op.drop_index(
        "ix_whatsapp_call_permissions_organization_id",
        table_name="whatsapp_call_permissions",
    )
    op.drop_index(
        "ix_whatsapp_call_permissions_id", table_name="whatsapp_call_permissions"
    )
    op.drop_table("whatsapp_call_permissions")
