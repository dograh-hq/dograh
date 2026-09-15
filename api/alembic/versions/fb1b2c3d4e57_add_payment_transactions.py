"""add payment_transactions table

Revision ID: fb1b2c3d4e57
Revises: fa1b2c3d4e56
Create Date: 2026-09-15 15:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fb1b2c3d4e57"
down_revision: Union[str, None] = "fa1b2c3d4e56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("amount_inr", sa.Float(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
        sa.Column("receipt", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("razorpay_signature", sa.String(length=256), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        sa.Column(
            "notes",
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_payment_transactions_org_status",
        "payment_transactions",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_org_status", table_name="payment_transactions")
    op.drop_table("payment_transactions")
