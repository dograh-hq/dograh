"""add subscription_plans and organization tier columns

Revision ID: eb1b2c3d4e59
Revises: ea1b2c3d4e58
Create Date: 2026-09-19 22:45:00.000000

"""

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "eb1b2c3d4e59"
down_revision: Union[str, None] = "ea1b2c3d4e58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create subscription_plans table
    subscription_plans_table = op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("slug", sa.String(length=64), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_usd", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("price_inr", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("billing_interval", sa.String(length=32), nullable=False, server_default=sa.text("'month'")),
        sa.Column("included_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False, server_default=sa.text("2")),
        sa.Column("max_agents", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("overage_rate_per_minute_usd", sa.Float(), nullable=False, server_default=sa.text("0.10")),
        sa.Column("allow_byok", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("features", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    # 2. Add columns to organizations
    op.add_column(
        "organizations",
        sa.Column(
            "subscription_tier",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'pay_as_you_go'"),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "subscription_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("billing_cycle_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("billing_cycle_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "monthly_minutes_used",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column("custom_concurrent_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("custom_monthly_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("custom_max_agents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("custom_allow_byok", sa.Boolean(), nullable=True),
    )

    # 3. Seed initial default plans
    op.bulk_insert(
        subscription_plans_table,
        [
            {
                "slug": "pay_as_you_go",
                "name": "Pay-As-You-Go",
                "description": "Flexible on-demand calling. Recharge your platform wallet and pay only for actual seconds connected.",
                "price_usd": 0.0,
                "price_inr": 0.0,
                "billing_interval": "month",
                "included_minutes": 0,
                "max_concurrent_calls": 2,
                "max_agents": 2,
                "overage_rate_per_minute_usd": 0.10,
                "allow_byok": True,
                "is_active": True,
                "is_public": True,
                "features": json.dumps([
                    "Pay per second from wallet",
                    "Bring Your Own Key (BYOK) supported",
                    "2 Concurrent Call lines",
                    "Up to 2 AI Voice Agents",
                    "Real-time Audio Recordings & Transcripts",
                    "Standard Community Support",
                ]),
            },
            {
                "slug": "starter",
                "name": "Starter Plan",
                "description": "Perfect for freelancers, doctors, clinics, and local businesses starting automated customer calling.",
                "price_usd": 49.0,
                "price_inr": 3999.0,
                "billing_interval": "month",
                "included_minutes": 400,
                "max_concurrent_calls": 3,
                "max_agents": 3,
                "overage_rate_per_minute_usd": 0.09,
                "allow_byok": True,
                "is_active": True,
                "is_public": True,
                "features": json.dumps([
                    "400 Included Calling Minutes / month",
                    "3 Simultaneous Concurrent Calls",
                    "Up to 3 Active AI Voice Agents",
                    "BYOK Supported",
                    "Webhook, CRM & Zapier Triggers",
                    "Overage rate: $0.09/min (₹7.5/min)",
                    "Priority Email Support",
                ]),
            },
            {
                "slug": "pro",
                "name": "Growth Pro",
                "description": "For growing sales teams, agencies, and outbound marketing campaigns requiring higher concurrency.",
                "price_usd": 149.0,
                "price_inr": 11999.0,
                "billing_interval": "month",
                "included_minutes": 1600,
                "max_concurrent_calls": 10,
                "max_agents": 10,
                "overage_rate_per_minute_usd": 0.08,
                "allow_byok": True,
                "is_active": True,
                "is_public": True,
                "features": json.dumps([
                    "1,600 Included Calling Minutes / month",
                    "10 Simultaneous Concurrent Lines",
                    "Up to 10 Active AI Voice Agents",
                    "High-volume Outbound Campaigns",
                    "BYOK or Platform Master Keys",
                    "Discounted Overage: $0.08/min (₹6.8/min)",
                    "Live Call Transfers & Knowledge Base RAG",
                ]),
            },
            {
                "slug": "enterprise",
                "name": "Enterprise Custom",
                "description": "Tailored for large organizations, call centers, and B2B aggregators needing dedicated capacity.",
                "price_usd": 399.0,
                "price_inr": 32999.0,
                "billing_interval": "month",
                "included_minutes": 5000,
                "max_concurrent_calls": 30,
                "max_agents": 50,
                "overage_rate_per_minute_usd": 0.06,
                "allow_byok": True,
                "is_active": True,
                "is_public": True,
                "features": json.dumps([
                    "5,000+ Monthly Minutes (or Custom Allocation)",
                    "30 to 100+ Concurrent Channels",
                    "Unlimited / Custom AI Voice Agents",
                    "Dedicated SIP Trunking & Custom LLM Fine-tunes",
                    "Custom Organization Enterprise Overrides",
                    "Lowest Overage: $0.06/min (₹5.0/min)",
                    "Dedicated SLA & 24/7 Priority Support",
                ]),
            },
        ],
    )


def downgrade() -> None:
    op.drop_column("organizations", "custom_allow_byok")
    op.drop_column("organizations", "custom_max_agents")
    op.drop_column("organizations", "custom_monthly_minutes")
    op.drop_column("organizations", "custom_concurrent_limit")
    op.drop_column("organizations", "monthly_minutes_used")
    op.drop_column("organizations", "billing_cycle_end")
    op.drop_column("organizations", "billing_cycle_start")
    op.drop_column("organizations", "subscription_status")
    op.drop_column("organizations", "subscription_tier")
    op.drop_table("subscription_plans")
