"""add payment_transactions table (Razorpay top-ups)

One row per Razorpay order; credited to the org's call-seconds balance on
verified payment. Additive — no change to existing tables.

Revision ID: e9c4a7b2f1d8
Revises: d8b3f1a2c6e4
Create Date: 2026-06-24 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9c4a7b2f1d8"
down_revision: Union[str, None] = "d8b3f1a2c6e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=True),
        sa.Column("pack_id", sa.String(length=64), nullable=True),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="created"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_payment_transactions_organization_id",
        "payment_transactions",
        ["organization_id"],
    )
    op.create_unique_constraint(
        "uq_payment_transactions_order_id",
        "payment_transactions",
        ["razorpay_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_organization_id", "payment_transactions")
    op.drop_table("payment_transactions")
