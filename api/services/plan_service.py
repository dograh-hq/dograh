"""Plan and Subscription Management Service.

Provides centralized limit calculations, plan resolution, quota checks,
and usage accounting across Pay-As-You-Go, Minute-based Subscriptions,
BYOK, and Organization-level Enterprise Customization.
"""

from dataclasses import dataclass
from datetime import datetime, UTC, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger
from sqlalchemy import select, update

from api.db import db_client
from api.db.models import OrganizationModel, SubscriptionPlanModel, WorkflowModel


@dataclass
class EffectiveLimits:
    organization_id: int
    tier: str
    tier_name: str
    subscription_status: str
    max_concurrent_calls: int
    max_agents: int
    included_minutes: int
    monthly_minutes_used: float
    minutes_remaining: float
    is_unlimited_minutes: bool
    overage_rate_per_minute_usd: float
    allow_byok: bool
    wallet_balance_usd: float


def normalize_plan_features(raw: Any) -> List[str]:
    """Ensure plan features are always returned as a clean list of strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if item]
    if isinstance(raw, str):
        import json
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if item]
            if isinstance(parsed, str):
                return [s.strip() for s in parsed.split("\n") if s.strip()]
        except Exception:
            return [s.strip() for s in raw.split("\n") if s.strip()]
    return []


DEFAULT_PAY_AS_YOU_GO = {
    "slug": "pay_as_you_go",
    "name": "Pay-As-You-Go",
    "description": "Flexible on-demand calling. Recharge platform wallet and pay only for actual seconds.",
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
    "features": [
        "Pay per second from wallet",
        "Bring Your Own Key (BYOK) supported",
        "2 Concurrent Call lines",
        "Up to 2 AI Voice Agents",
        "Full Call Recordings & Transcripts",
    ],
}


class PlanService:
    async def ensure_default_plans(self) -> None:
        """Ensure subscription_plans table exists and seed default plans if empty."""
        from sqlalchemy import text
        try:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(64) UNIQUE NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    description TEXT,
                    price_usd FLOAT NOT NULL DEFAULT 0.0,
                    price_inr FLOAT NOT NULL DEFAULT 0.0,
                    billing_interval VARCHAR(32) NOT NULL DEFAULT 'month',
                    included_minutes INT NOT NULL DEFAULT 0,
                    max_concurrent_calls INT NOT NULL DEFAULT 2,
                    max_agents INT NOT NULL DEFAULT 1,
                    overage_rate_per_minute_usd FLOAT NOT NULL DEFAULT 0.10,
                    allow_byok BOOLEAN NOT NULL DEFAULT true,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    is_public BOOLEAN NOT NULL DEFAULT true,
                    features JSON NOT NULL DEFAULT '[]'::json,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """,
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS subscription_tier VARCHAR(64) DEFAULT 'pay_as_you_go'",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(32) DEFAULT 'active'",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS billing_cycle_start TIMESTAMPTZ",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS billing_cycle_end TIMESTAMPTZ",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS monthly_minutes_used FLOAT DEFAULT 0.0",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_concurrent_limit INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_monthly_minutes INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_max_agents INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_allow_byok BOOLEAN",
            ]
            async with db_client.async_session() as session:
                for stmt in statements:
                    try:
                        await session.execute(text(stmt))
                    except Exception:
                        pass
                await session.commit()

            plans = await self.list_plans(include_inactive=True)
            if not plans:
                default_plans = [
                    DEFAULT_PAY_AS_YOU_GO,
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
                        "features": [
                            "400 Included Calling Minutes / month",
                            "3 Simultaneous Concurrent Calls",
                            "Up to 3 Active AI Voice Agents",
                            "BYOK Supported",
                            "Webhook, CRM & Zapier Triggers",
                            "Overage rate: $0.09/min (₹7.5/min)",
                            "Priority Email Support",
                        ],
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
                        "features": [
                            "1,600 Included Calling Minutes / month",
                            "10 Simultaneous Concurrent Lines",
                            "Up to 10 Active AI Voice Agents",
                            "High-volume Outbound Campaigns",
                            "BYOK or Platform Master Keys",
                            "Discounted Overage: $0.08/min (₹6.8/min)",
                            "Live Call Transfers & Knowledge Base RAG",
                        ],
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
                        "features": [
                            "5,000+ Monthly Minutes (or Custom Allocation)",
                            "30 to 100+ Concurrent Channels",
                            "Unlimited / Custom AI Voice Agents",
                            "Dedicated SIP Trunking & Custom LLM Fine-tunes",
                            "Custom Organization Enterprise Overrides",
                            "Lowest Overage: $0.06/min (₹5.0/min)",
                            "Dedicated SLA & 24/7 Priority Support",
                        ],
                    },
                ]

                async with db_client.async_session() as session:
                    for p in default_plans:
                        session.add(
                            SubscriptionPlanModel(
                                slug=p["slug"],
                                name=p["name"],
                                description=p["description"],
                                price_usd=p["price_usd"],
                                price_inr=p["price_inr"],
                                billing_interval=p["billing_interval"],
                                included_minutes=p["included_minutes"],
                                max_concurrent_calls=p["max_concurrent_calls"],
                                max_agents=p["max_agents"],
                                overage_rate_per_minute_usd=p["overage_rate_per_minute_usd"],
                                allow_byok=p["allow_byok"],
                                is_active=p["is_active"],
                                is_public=p["is_public"],
                                features=p["features"],
                            )
                        )
                    await session.commit()
                logger.info("Successfully seeded default SaaS subscription plans.")
        except Exception as e:
            logger.warning("ensure_default_plans warning: {}", e)

    async def get_plan_by_slug(self, slug: str) -> Optional[SubscriptionPlanModel]:
        """Fetch a subscription plan by its slug."""
        async with db_client.async_session() as session:
            stmt = select(SubscriptionPlanModel).where(SubscriptionPlanModel.slug == slug)
            res = await session.execute(stmt)
            return res.scalars().first()

    async def list_plans(self, include_inactive: bool = False) -> List[SubscriptionPlanModel]:
        """List all subscription plans."""
        async with db_client.async_session() as session:
            stmt = select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.price_usd.asc())
            if not include_inactive:
                stmt = stmt.where(SubscriptionPlanModel.is_active.is_(True))
            res = await session.execute(stmt)
            return list(res.scalars().all())

    async def get_effective_limits(self, organization_id: int) -> EffectiveLimits:
        """Resolve effective limits for an organization, incorporating custom enterprise overrides."""
        org = await db_client.get_organization_by_id(organization_id)
        if not org:
            return EffectiveLimits(
                organization_id=organization_id,
                tier="pay_as_you_go",
                tier_name="Pay-As-You-Go",
                subscription_status="active",
                max_concurrent_calls=2,
                max_agents=2,
                included_minutes=0,
                monthly_minutes_used=0.0,
                minutes_remaining=0.0,
                is_unlimited_minutes=False,
                overage_rate_per_minute_usd=0.10,
                allow_byok=True,
                wallet_balance_usd=0.0,
            )

        tier_slug = getattr(org, "subscription_tier", "pay_as_you_go") or "pay_as_you_go"
        plan = await self.get_plan_by_slug(tier_slug)

        # Plan base values or fallback
        base_name = plan.name if plan else tier_slug.replace("_", " ").title()
        base_concurrency = plan.max_concurrent_calls if plan else 2
        base_agents = plan.max_agents if plan else 2
        base_minutes = plan.included_minutes if plan else 0
        base_overage = plan.overage_rate_per_minute_usd if plan else 0.10
        base_allow_byok = plan.allow_byok if plan else True

        # Enterprise Custom Overrides (take precedence over standard plan!)
        effective_concurrency = (
            org.custom_concurrent_limit
            if getattr(org, "custom_concurrent_limit", None) is not None
            else base_concurrency
        )
        effective_agents = (
            org.custom_max_agents
            if getattr(org, "custom_max_agents", None) is not None
            else base_agents
        )
        effective_allow_byok = (
            org.custom_allow_byok
            if getattr(org, "custom_allow_byok", None) is not None
            else base_allow_byok
        )

        is_unlimited = False
        if getattr(org, "custom_monthly_minutes", None) is not None:
            if org.custom_monthly_minutes == -1:
                is_unlimited = True
                effective_minutes = 999999
            else:
                effective_minutes = org.custom_monthly_minutes
        else:
            effective_minutes = base_minutes

        # Custom price per second override if configured on organization
        if getattr(org, "price_per_second_usd", None) and org.price_per_second_usd > 0:
            effective_overage = round(float(org.price_per_second_usd) * 60.0, 4)
        else:
            effective_overage = base_overage

        used_mins = float(getattr(org, "monthly_minutes_used", 0.0) or 0.0)
        remaining_mins = 999999.0 if is_unlimited else max(0.0, effective_minutes - used_mins)
        wallet_bal = float(getattr(org, "wallet_balance_usd", 0.0) or 0.0)

        return EffectiveLimits(
            organization_id=organization_id,
            tier=tier_slug,
            tier_name=base_name,
            subscription_status=getattr(org, "subscription_status", "active") or "active",
            max_concurrent_calls=max(1, effective_concurrency),
            max_agents=max(1, effective_agents),
            included_minutes=effective_minutes,
            monthly_minutes_used=used_mins,
            minutes_remaining=remaining_mins,
            is_unlimited_minutes=is_unlimited,
            overage_rate_per_minute_usd=effective_overage,
            allow_byok=effective_allow_byok,
            wallet_balance_usd=wallet_bal,
        )

    async def validate_can_create_workflow(self, organization_id: int) -> tuple[bool, str]:
        """Validate if the organization has capacity to create another AI Agent workflow."""
        limits = await self.get_effective_limits(organization_id)
        current_count = await db_client.get_workflow_count(organization_id)

        if current_count >= limits.max_agents:
            return (
                False,
                f"Agent limit reached ({limits.max_agents} agents max on your {limits.tier_name} plan). "
                f"Please upgrade your plan or delete an unused agent.",
            )
        return True, ""

    async def assign_organization_plan(
        self,
        organization_id: int,
        plan_slug: str,
        custom_concurrent_limit: Optional[int] = None,
        custom_monthly_minutes: Optional[int] = None,
        custom_max_agents: Optional[int] = None,
        custom_allow_byok: Optional[bool] = None,
        reset_minutes_used: bool = False,
    ) -> EffectiveLimits:
        """Assign or update the subscription plan and enterprise overrides for an organization."""
        async with db_client.async_session() as session:
            stmt = select(OrganizationModel).where(OrganizationModel.id == organization_id)
            res = await session.execute(stmt)
            org = res.scalars().first()
            if not org:
                raise ValueError(f"Organization {organization_id} not found")

            org.subscription_tier = plan_slug
            org.subscription_status = "active"

            # Set 30-day billing cycle dates if not present
            now = datetime.now(UTC)
            org.billing_cycle_start = now
            org.billing_cycle_end = now + timedelta(days=30)

            if reset_minutes_used:
                org.monthly_minutes_used = 0.0

            if custom_concurrent_limit is not None:
                org.custom_concurrent_limit = custom_concurrent_limit
            if custom_monthly_minutes is not None:
                org.custom_monthly_minutes = custom_monthly_minutes
            if custom_max_agents is not None:
                org.custom_max_agents = custom_max_agents
            if custom_allow_byok is not None:
                org.custom_allow_byok = custom_allow_byok

            await session.commit()

        logger.info(
            "Updated plan for organization {} to tier={} with custom limits (concurrency={}, mins={}, agents={})",
            organization_id,
            plan_slug,
            custom_concurrent_limit,
            custom_monthly_minutes,
            custom_max_agents,
        )
        return await self.get_effective_limits(organization_id)

    async def record_run_billing(
        self,
        organization_id: int,
        duration_seconds: float,
        rate_per_min: float,
    ) -> Dict[str, Any]:
        """Allocate call cost between monthly subscription included minutes and platform wallet overage."""
        limits = await self.get_effective_limits(organization_id)
        call_duration_minutes = duration_seconds / 60.0

        charged_to = "wallet"
        plan_minutes_deducted = 0.0
        overage_minutes = 0.0
        wallet_cost_usd = 0.0
        new_wallet_balance = limits.wallet_balance_usd

        if limits.minutes_remaining > 0:
            if limits.minutes_remaining >= call_duration_minutes:
                # Fully covered under monthly subscription minutes!
                plan_minutes_deducted = call_duration_minutes
                charged_to = "subscription_minutes"
            else:
                # Partially covered by subscription minutes, remainder charged to wallet!
                plan_minutes_deducted = limits.minutes_remaining
                overage_minutes = call_duration_minutes - plan_minutes_deducted
                wallet_cost_usd = round(overage_minutes * limits.overage_rate_per_minute_usd, 4)
                charged_to = "split"
        else:
            # Fully charged to wallet (Pay-As-You-Go or overage)
            overage_minutes = call_duration_minutes
            wallet_cost_usd = round(call_duration_minutes * rate_per_min, 4)
            charged_to = "wallet"

        # 1. Update monthly minutes used if applicable
        if plan_minutes_deducted > 0:
            async with db_client.async_session() as session:
                stmt = (
                    update(OrganizationModel)
                    .where(OrganizationModel.id == organization_id)
                    .values(
                        monthly_minutes_used=OrganizationModel.monthly_minutes_used
                        + plan_minutes_deducted
                    )
                )
                await session.execute(stmt)
                await session.commit()

        # 2. Update wallet balance if overage or pay-as-you-go cost incurred
        if wallet_cost_usd > 0:
            new_wallet_balance = await db_client.update_wallet_balance(
                organization_id, -wallet_cost_usd
            )

        return {
            "charged_to": charged_to,
            "call_duration_seconds": duration_seconds,
            "call_duration_minutes": round(call_duration_minutes, 2),
            "plan_minutes_deducted": round(plan_minutes_deducted, 2),
            "overage_minutes": round(overage_minutes, 2),
            "wallet_cost_usd": wallet_cost_usd,
            "wallet_balance_after": new_wallet_balance,
            "effective_tier": limits.tier,
            "overage_rate_per_minute": limits.overage_rate_per_minute_usd,
        }


plan_service = PlanService()
