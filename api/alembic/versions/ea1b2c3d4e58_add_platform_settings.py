"""add platform settings table

Revision ID: ea1b2c3d4e58
Revises: e9a1b2c3d4e5
Create Date: 2026-09-16 10:25:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ea1b2c3d4e58"
down_revision: Union[str, None] = "fb1b2c3d4e57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create platform_settings table
    platform_settings_table = op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("ix_platform_settings_key", "platform_settings", ["key"])

    # 2. Seed initial default values
    op.bulk_insert(
        platform_settings_table,
        [
            {
                "key": "usd_to_inr_rate",
                "value": "86.0",
                "description": "USD to INR exchange rate for wallet top-ups and Razorpay payments",
            },
            {
                "key": "gst_percentage",
                "value": "18.0",
                "description": "Goods and Services Tax (GST) percentage applied to top-up orders",
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_platform_settings_key", table_name="platform_settings")
    op.drop_table("platform_settings")
