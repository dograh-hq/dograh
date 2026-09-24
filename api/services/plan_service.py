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
    plan_credits_remaining_usd: float = 0.0
    plan_credits_monthly_usd: float = 0.0
    included_phone_numbers: int = 0
    byok_platform_fee_per_minute_usd: float = 0.04
    allow_live_transfer: bool = False
    allow_sip_trunking: bool = False
    custom_monthly_price_usd: Optional[float] = None


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
    "description": "Flexible on-demand calling with 15 free trial minutes included. Recharge platform wallet for additional seconds.",
    "price_usd": 0.0,
    "price_inr": 0.0,
    "billing_interval": "month",
    "included_minutes": 15,
    "monthly_credits_usd": 0.0,
    "included_phone_numbers": 0,
    "max_concurrent_calls": 2,
    "max_agents": 2,
    "overage_rate_per_minute_usd": 0.10,
    "byok_platform_fee_per_minute_usd": 0.05,
    "allow_byok": True,
    "allow_live_transfer": False,
    "allow_sip_trunking": False,
    "is_active": True,
    "is_public": True,
    "features": [
        "15 Free Trial Minutes Included",
        "Pay per second from platform wallet",
        "BYOK supported ($0.05/min platform fee)",
        "2 Concurrent Call lines",
        "Up to 2 AI Voice Agents",
        "Full Call Recordings & Transcripts",
    ],
}


class PlanService:
    async def ensure_default_plans(self) -> None:
        """Ensure subscription_plans table exists, columns are up to date, and seed default plans."""
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
                    monthly_credits_usd FLOAT NOT NULL DEFAULT 0.0,
                    included_phone_numbers INT NOT NULL DEFAULT 0,
                    max_concurrent_calls INT NOT NULL DEFAULT 2,
                    max_agents INT NOT NULL DEFAULT 1,
                    overage_rate_per_minute_usd FLOAT NOT NULL DEFAULT 0.10,
                    byok_platform_fee_per_minute_usd FLOAT NOT NULL DEFAULT 0.04,
                    allow_byok BOOLEAN NOT NULL DEFAULT true,
                    allow_live_transfer BOOLEAN NOT NULL DEFAULT false,
                    allow_sip_trunking BOOLEAN NOT NULL DEFAULT false,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    is_public BOOLEAN NOT NULL DEFAULT true,
                    features JSON NOT NULL DEFAULT '[]'::json,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """,
                # subscription_plans columns
                "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS monthly_credits_usd FLOAT DEFAULT 0.0",
                "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS included_phone_numbers INT DEFAULT 0",
                "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS byok_platform_fee_per_minute_usd FLOAT DEFAULT 0.04",
                "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS allow_live_transfer BOOLEAN DEFAULT false",
                "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS allow_sip_trunking BOOLEAN DEFAULT false",
                # organizations columns
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS subscription_tier VARCHAR(64) DEFAULT 'simple_trial'",
                "ALTER TABLE organizations ALTER COLUMN subscription_tier SET DEFAULT 'simple_trial'",
                "ALTER TABLE organizations ALTER COLUMN wallet_balance_usd SET DEFAULT 0.0",
                "UPDATE organizations SET wallet_balance_usd = 0.0 WHERE subscription_tier = 'simple_trial' AND wallet_balance_usd = 10.0",
                "UPDATE organizations SET subscription_tier = 'simple_trial', wallet_balance_usd = 0.0 WHERE subscription_tier = 'pay_as_you_go' AND wallet_balance_usd = 10.0 AND (monthly_minutes_used = 0.0 OR monthly_minutes_used IS NULL)",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(32) DEFAULT 'active'",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS billing_cycle_start TIMESTAMPTZ",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS billing_cycle_end TIMESTAMPTZ",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS monthly_minutes_used FLOAT DEFAULT 0.0",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_concurrent_limit INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_monthly_minutes INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_max_agents INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_allow_byok BOOLEAN",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS plan_credits_remaining_usd FLOAT DEFAULT 0.0",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS plan_credits_monthly_usd FLOAT DEFAULT 0.0",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS plan_credits_reset_at TIMESTAMPTZ",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_monthly_price_usd FLOAT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_monthly_credits_usd FLOAT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_included_phone_numbers INT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_byok_platform_fee_usd FLOAT",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_allow_live_transfer BOOLEAN",
                "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS custom_allow_sip_trunking BOOLEAN",
                # telephony_phone_numbers columns
                "ALTER TABLE telephony_phone_numbers ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
                "ALTER TABLE telephony_phone_numbers ADD COLUMN IF NOT EXISTS next_rental_billing_at TIMESTAMPTZ",
                "ALTER TABLE telephony_phone_numbers ADD COLUMN IF NOT EXISTS rental_status VARCHAR(32) DEFAULT 'active'",
            ]
            async with db_client.async_session() as session:
                for stmt in statements:
                    try:
                        await session.execute(text(stmt))
                    except Exception:
                        pass
                await session.commit()

            default_plans = [
                DEFAULT_PAY_AS_YOU_GO,
                {
                    "slug": "starter",
                    "name": "Starter Plan",
                    "description": "Perfect for freelancers, clinics, and local businesses starting automated AI customer calling.",
                    "price_usd": 49.0,
                    "price_inr": 3999.0,
                    "billing_interval": "month",
                    "included_minutes": 400,
                    "monthly_credits_usd": 49.0,
                    "included_phone_numbers": 1,
                    "max_concurrent_calls": 3,
                    "max_agents": 3,
                    "overage_rate_per_minute_usd": 0.09,
                    "byok_platform_fee_per_minute_usd": 0.04,
                    "allow_byok": True,
                    "allow_live_transfer": False,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "$49.00 Included Call Credits / month (~408 standard mins)",
                        "1 Included Dedicated Platform Phone Number",
                        "3 Simultaneous Concurrent Calls",
                        "Up to 3 Active AI Voice Agents",
                        "BYOK Supported ($0.04/min platform fee)",
                        "Webhook, CRM & Zapier Triggers",
                        "Priority Email Support",
                    ],
                },
                {
                    "slug": "pro",
                    "name": "Growth Pro",
                    "description": "For growing sales teams, agencies, and outbound campaigns requiring higher concurrency.",
                    "price_usd": 149.0,
                    "price_inr": 11999.0,
                    "billing_interval": "month",
                    "included_minutes": 1600,
                    "monthly_credits_usd": 160.0,
                    "included_phone_numbers": 2,
                    "max_concurrent_calls": 10,
                    "max_agents": 10,
                    "overage_rate_per_minute_usd": 0.08,
                    "byok_platform_fee_per_minute_usd": 0.03,
                    "allow_byok": True,
                    "allow_live_transfer": True,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "$160.00 Included Call Credits / month (~1,333 standard mins)",
                        "2 Included Dedicated Platform Phone Numbers",
                        "10 Simultaneous Concurrent Lines",
                        "Up to 10 Active AI Voice Agents",
                        "Live Call Transfers & Knowledge Base RAG",
                        "BYOK Supported ($0.03/min platform fee)",
                        "High-volume Outbound Campaigns",
                    ],
                },
                {
                    "slug": "enterprise",
                    "name": "Enterprise Custom",
                    "description": "Tailored for large organizations, call centers, and B2B aggregators needing custom capacity.",
                    "price_usd": 0.0,
                    "price_inr": 0.0,
                    "billing_interval": "month",
                    "included_minutes": 5000,
                    "monthly_credits_usd": 0.0,
                    "included_phone_numbers": 5,
                    "max_concurrent_calls": 30,
                    "max_agents": 50,
                    "overage_rate_per_minute_usd": 0.06,
                    "byok_platform_fee_per_minute_usd": 0.02,
                    "allow_byok": True,
                    "allow_live_transfer": True,
                    "allow_sip_trunking": True,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "Custom Credits & Pricing Configured per Organization",
                        "5+ Dedicated Platform Phone Numbers",
                        "30+ High-throughput Concurrent Lines",
                        "50+ Active AI Voice Agents",
                        "Dedicated SIP Trunking & Custom LLM Fine-tunes",
                        "Live Call Transfers & Inbound Routing",
                        "Dedicated SLA & 24/7 Priority Support",
                    ],
                },
                # --- Simple Minute-Based Business Plans (Callio Native Plans) ---
                {
                    "slug": "simple_trial",
                    "name": "Free Trial",
                    "description": "15 free calling minutes to test AI voice agents in browser or quick demo calls.",
                    "price_usd": 0.0,
                    "price_inr": 0.0,
                    "billing_interval": "month",
                    "included_minutes": 15,
                    "monthly_credits_usd": 0.0,
                    "included_phone_numbers": 0,
                    "max_concurrent_calls": 1,
                    "max_agents": 1,
                    "overage_rate_per_minute_usd": 0.10,
                    "byok_platform_fee_per_minute_usd": 0.04,
                    "allow_byok": True,
                    "allow_live_transfer": False,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "15 Free Calling Minutes Included",
                        "1 Active AI Voice Agent",
                        "1 Simultaneous Call Line",
                        "WebRTC In-Browser Voice Testing",
                        "Instant Setup, No Credit Card Required",
                    ],
                },
                {
                    "slug": "simple_starter",
                    "name": "Starter",
                    "description": "Try Callio on your real leads. 40 calling minutes included.",
                    "price_usd": 6.0,
                    "price_inr": 499.0,
                    "billing_interval": "month",
                    "included_minutes": 40,
                    "monthly_credits_usd": 6.0,
                    "included_phone_numbers": 0,
                    "max_concurrent_calls": 1,
                    "max_agents": 2,
                    "overage_rate_per_minute_usd": 0.10,
                    "byok_platform_fee_per_minute_usd": 0.04,
                    "allow_byok": True,
                    "allow_live_transfer": False,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "40 calling minutes",
                        "All AI Callers included",
                        "Test calls to your own number",
                        "Call summaries and intent detection",
                        "Valid for 30 days",
                    ],
                },
                # Top-Up Minute Pack (100 Min @ ₹6.50/min)
                {
                    "slug": "simple_topup_100",
                    "name": "Minute Top-Up (100 Min)",
                    "description": "Flexible on-demand top-up pack. 100 calling minutes at flat ₹6.50/minute with 12 months validity.",
                    "price_usd": 7.65,
                    "price_inr": 650.0,
                    "billing_interval": "month",
                    "included_minutes": 100,
                    "monthly_credits_usd": 7.65,
                    "included_phone_numbers": 0,
                    "max_concurrent_calls": 3,
                    "max_agents": 5,
                    "overage_rate_per_minute_usd": 0.076,
                    "byok_platform_fee_per_minute_usd": 0.03,
                    "allow_byok": True,
                    "allow_live_transfer": True,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "100 Calling Minutes Included",
                        "Flat ₹6.50 per minute (no hidden charges)",
                        "Per-second billing after connect",
                        "3 Simultaneous Outbound Calling Lines",
                        "Up to 5 Active AI Voice Agents",
                        "Call recordings and intent analytics",
                        "Minutes valid for 12 months (Rollover)",
                    ],
                },
                {
                    "slug": "simple_growth",
                    "name": "Growth Pack (150 Min)",
                    "description": "For regular, higher-volume calling. Pay for what you use at flat ₹6.50/min.",
                    "price_usd": 11.5,
                    "price_inr": 975.0,
                    "billing_interval": "month",
                    "included_minutes": 150,
                    "monthly_credits_usd": 11.5,
                    "included_phone_numbers": 1,
                    "max_concurrent_calls": 5,
                    "max_agents": 10,
                    "overage_rate_per_minute_usd": 0.076,
                    "byok_platform_fee_per_minute_usd": 0.03,
                    "allow_byok": True,
                    "allow_live_transfer": True,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "150 Calling Minutes Included",
                        "Flat ₹6.50 per minute billing",
                        "1 Included Dedicated Phone Number",
                        "5 Simultaneous Concurrent Lines",
                        "Up to 10 Active AI Voice Agents",
                        "Call recordings and transcripts",
                        "Minutes valid for 12 months",
                    ],
                },
                {
                    "slug": "simple_agency",
                    "name": "Business",
                    "description": "For teams calling every day with dedicated numbers and maximum capacity.",
                    "price_usd": 240.0,
                    "price_inr": 19999.0,
                    "billing_interval": "month",
                    "included_minutes": 10000,
                    "monthly_credits_usd": 240.0,
                    "included_phone_numbers": 1,
                    "max_concurrent_calls": 15,
                    "max_agents": 30,
                    "overage_rate_per_minute_usd": 0.07,
                    "byok_platform_fee_per_minute_usd": 0.02,
                    "allow_byok": True,
                    "allow_live_transfer": True,
                    "allow_sip_trunking": False,
                    "is_active": True,
                    "is_public": True,
                    "features": [
                        "Unlimited calling under fair use (10,000 mins)",
                        "Dedicated calling number included",
                        "15 High-throughput concurrent lines",
                        "Team access and roles",
                        "Live call transfers & custom caller setup",
                        "Priority support & dedicated SLA",
                    ],
                },
            ]

            async with db_client.async_session() as session:
                for p in default_plans:
                    stmt = select(SubscriptionPlanModel).where(SubscriptionPlanModel.slug == p["slug"])
                    res = await session.execute(stmt)
                    existing = res.scalars().first()
                    if not existing:
                        session.add(
                            SubscriptionPlanModel(
                                slug=p["slug"],
                                name=p["name"],
                                description=p["description"],
                                price_usd=p["price_usd"],
                                price_inr=p["price_inr"],
                                billing_interval=p["billing_interval"],
                                included_minutes=p["included_minutes"],
                                monthly_credits_usd=p["monthly_credits_usd"],
                                included_phone_numbers=p["included_phone_numbers"],
                                max_concurrent_calls=p["max_concurrent_calls"],
                                max_agents=p["max_agents"],
                                overage_rate_per_minute_usd=p["overage_rate_per_minute_usd"],
                                byok_platform_fee_per_minute_usd=p["byok_platform_fee_per_minute_usd"],
                                allow_byok=p["allow_byok"],
                                allow_live_transfer=p["allow_live_transfer"],
                                allow_sip_trunking=p["allow_sip_trunking"],
                                is_active=p["is_active"],
                                is_public=p["is_public"],
                                features=p["features"],
                            )
                        )
                    else:
                        # Always sync mutable fields so code changes take effect
                        existing.allow_byok = p["allow_byok"]
                        existing.allow_live_transfer = p["allow_live_transfer"]
                        existing.allow_sip_trunking = p["allow_sip_trunking"]
                        existing.is_active = p["is_active"]
                        existing.is_public = p["is_public"]
                await session.commit()
            logger.info("Successfully ensured default subscription plans.")
        except Exception as e:
            logger.warning("ensure_default_plans warning: {}", e)

    async def get_plan_by_slug(self, slug: str) -> Optional[SubscriptionPlanModel]:
        """Fetch a subscription plan by its slug."""
        async with db_client.async_session() as session:
            stmt = select(SubscriptionPlanModel).where(SubscriptionPlanModel.slug == slug)
            res = await session.execute(stmt)
            return res.scalars().first()

    async def list_plans(
        self,
        include_inactive: bool = False,
        category: Optional[str] = None,
    ) -> List[SubscriptionPlanModel]:
        """List subscription plans, optionally filtered by category ('simple' or 'developer')."""
        async with db_client.async_session() as session:
            stmt = select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.price_usd.asc())
            if not include_inactive:
                stmt = stmt.where(SubscriptionPlanModel.is_active.is_(True))
            res = await session.execute(stmt)
            all_plans = list(res.scalars().all())

            if category == "simple":
                return [p for p in all_plans if p.slug.startswith("simple_")]
            elif category == "developer":
                return [p for p in all_plans if not p.slug.startswith("simple_")]
            return all_plans

    async def get_effective_limits(self, organization_id: int) -> EffectiveLimits:
        """Resolve effective limits for an organization, incorporating custom enterprise overrides."""
        org = await db_client.get_organization_by_id(organization_id)
        if not org:
            return EffectiveLimits(
                organization_id=organization_id,
                tier="simple_trial",
                tier_name="Free Trial",
                subscription_status="active",
                max_concurrent_calls=1,
                max_agents=1,
                included_minutes=15,
                monthly_minutes_used=0.0,
                minutes_remaining=15.0,
                is_unlimited_minutes=False,
                overage_rate_per_minute_usd=0.10,
                allow_byok=False,
                wallet_balance_usd=0.0,
                plan_credits_remaining_usd=0.0,
                plan_credits_monthly_usd=0.0,
                included_phone_numbers=0,
                byok_platform_fee_per_minute_usd=0.04,
                allow_live_transfer=False,
                allow_sip_trunking=False,
                custom_monthly_price_usd=None,
            )

        tier_slug = getattr(org, "subscription_tier", "simple_trial") or "simple_trial"
        plan = await self.get_plan_by_slug(tier_slug)

        # Plan base values or fallback
        base_name = plan.name if plan else tier_slug.replace("_", " ").title()
        base_concurrency = plan.max_concurrent_calls if plan else 2
        base_agents = plan.max_agents if plan else 2
        base_minutes = plan.included_minutes if plan else (15 if tier_slug in ("pay_as_you_go", "simple_trial") else 0)
        if base_minutes == 0 and tier_slug in ("pay_as_you_go", "simple_trial"):
            base_minutes = 15
        base_overage = plan.overage_rate_per_minute_usd if plan else 0.10
        base_allow_byok = plan.allow_byok if plan else True
        base_credits = getattr(plan, "monthly_credits_usd", 0.0) or 0.0
        base_phone_numbers = getattr(plan, "included_phone_numbers", 0) or 0
        base_byok_fee = getattr(plan, "byok_platform_fee_per_minute_usd", 0.04) or 0.04
        base_live_transfer = getattr(plan, "allow_live_transfer", False) or False
        base_sip_trunking = getattr(plan, "allow_sip_trunking", False) or False

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
        if base_agents == -1 or getattr(org, "custom_max_agents", None) == -1:
            effective_agents = 999999

        effective_allow_byok = (
            org.custom_allow_byok
            if getattr(org, "custom_allow_byok", None) is not None
            else base_allow_byok
        )
        effective_monthly_credits = (
            float(org.custom_monthly_credits_usd)
            if getattr(org, "custom_monthly_credits_usd", None) is not None
            else base_credits
        )
        if base_credits == -1 or getattr(org, "custom_monthly_credits_usd", None) == -1:
            effective_monthly_credits = 999999.0

        effective_phone_numbers = (
            int(org.custom_included_phone_numbers)
            if getattr(org, "custom_included_phone_numbers", None) is not None
            else base_phone_numbers
        )
        effective_byok_fee = (
            float(org.custom_byok_platform_fee_usd)
            if getattr(org, "custom_byok_platform_fee_usd", None) is not None
            else base_byok_fee
        )
        effective_live_transfer = (
            bool(org.custom_allow_live_transfer)
            if getattr(org, "custom_allow_live_transfer", None) is not None
            else base_live_transfer
        )
        effective_sip_trunking = (
            bool(org.custom_allow_sip_trunking)
            if getattr(org, "custom_allow_sip_trunking", None) is not None
            else base_sip_trunking
        )
        custom_price = (
            float(org.custom_monthly_price_usd)
            if getattr(org, "custom_monthly_price_usd", None) is not None
            else None
        )

        is_unlimited = False
        if base_minutes == -1 or getattr(org, "custom_monthly_minutes", None) == -1:
            is_unlimited = True
            effective_minutes = 999999
        elif getattr(org, "custom_monthly_minutes", None) is not None:
            # Custom minutes include top-up packs on top of plan base minutes
            effective_minutes = base_minutes + int(org.custom_monthly_minutes)
        else:
            effective_minutes = base_minutes

        if getattr(org, "price_per_second_usd", None) and org.price_per_second_usd > 0:
            effective_overage = round(float(org.price_per_second_usd) * 60.0, 4)
        else:
            effective_overage = base_overage

        used_mins = float(getattr(org, "monthly_minutes_used", 0.0) or 0.0)
        wallet_bal = float(getattr(org, "wallet_balance_usd", 0.0) or 0.0)
        credits_remaining = float(getattr(org, "plan_credits_remaining_usd", 0.0) or 0.0)

        # In addition to plan allowance, wallet balance can directly fund calling minutes at overage rate
        # For free trial (simple_trial), wallet balance does not add free minutes unless it's a paid top-up
        wallet_minutes = (
            round(wallet_bal / effective_overage, 1)
            if (effective_overage > 0 and wallet_bal > 0 and tier_slug != "simple_trial")
            else 0.0
        )

        plan_remaining_mins = 999999.0 if is_unlimited else max(0.0, effective_minutes - used_mins)
        # Total remaining minutes is plan remaining + wallet backed calling minutes
        remaining_mins = 999999.0 if is_unlimited else round(plan_remaining_mins + wallet_minutes, 1)
        total_allowance_minutes = 999999 if is_unlimited else int(effective_minutes + wallet_minutes)

        return EffectiveLimits(
            organization_id=organization_id,
            tier=tier_slug,
            tier_name=base_name,
            subscription_status=getattr(org, "subscription_status", "active") or "active",
            max_concurrent_calls=max(1, effective_concurrency),
            max_agents=max(1, effective_agents),
            included_minutes=total_allowance_minutes,
            monthly_minutes_used=used_mins,
            minutes_remaining=remaining_mins,
            is_unlimited_minutes=is_unlimited,
            overage_rate_per_minute_usd=effective_overage,
            allow_byok=effective_allow_byok,
            wallet_balance_usd=wallet_bal,
            plan_credits_remaining_usd=credits_remaining,
            plan_credits_monthly_usd=effective_monthly_credits,
            included_phone_numbers=effective_phone_numbers,
            byok_platform_fee_per_minute_usd=effective_byok_fee,
            allow_live_transfer=effective_live_transfer,
            allow_sip_trunking=effective_sip_trunking,
            custom_monthly_price_usd=custom_price,
        )

    async def validate_can_create_workflow(self, organization_id: int) -> tuple[bool, str]:
        """Validate if the organization has capacity to create another AI Agent workflow."""
        limits = await self.get_effective_limits(organization_id)
        current_count = await db_client.get_workflow_count(organization_id)

        if limits.max_agents != -1 and limits.max_agents < 999999 and current_count >= limits.max_agents:
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
        custom_monthly_price_usd: Optional[float] = None,
        custom_monthly_credits_usd: Optional[float] = None,
        custom_included_phone_numbers: Optional[int] = None,
        custom_byok_platform_fee_usd: Optional[float] = None,
        custom_allow_live_transfer: Optional[bool] = None,
        custom_allow_sip_trunking: Optional[bool] = None,
        reset_credits: bool = True,
        reset_minutes_used: bool = True,
        **kwargs: Any,
    ) -> EffectiveLimits:
        """Assign or update the subscription plan and enterprise overrides for an organization."""
        plan = await self.get_plan_by_slug(plan_slug)

        async with db_client.async_session() as session:
            stmt = select(OrganizationModel).where(OrganizationModel.id == organization_id)
            res = await session.execute(stmt)
            org = res.scalars().first()
            if not org:
                raise ValueError(f"Organization {organization_id} not found")

            # Check if this is a top-up pack on an existing active paid subscription
            if plan_slug.startswith("simple_topup") and org.subscription_tier in ("simple_starter", "simple_growth", "simple_agency"):
                # Preserve base tier and add minutes to custom_monthly_minutes
                topup_mins = plan.included_minutes if plan else 100
                current_custom = int(org.custom_monthly_minutes or 0)
                org.custom_monthly_minutes = current_custom + topup_mins
            else:
                org.subscription_tier = plan_slug
                if plan_slug == "simple_trial" and float(getattr(org, "wallet_balance_usd", 0.0) or 0.0) == 10.0:
                    org.wallet_balance_usd = 0.0

            org.subscription_status = "active"

            now = datetime.now(UTC)
            org.billing_cycle_start = now
            org.billing_cycle_end = now + timedelta(days=30)

            # Resolve monthly credits
            monthly_credits = 0.0
            if custom_monthly_credits_usd is not None:
                monthly_credits = float(custom_monthly_credits_usd)
            elif plan and getattr(plan, "monthly_credits_usd", 0.0):
                monthly_credits = float(plan.monthly_credits_usd)

            org.plan_credits_monthly_usd = monthly_credits
            if reset_credits:
                org.plan_credits_remaining_usd = monthly_credits
                org.plan_credits_reset_at = now + timedelta(days=30)

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
            if custom_monthly_price_usd is not None:
                org.custom_monthly_price_usd = custom_monthly_price_usd
            if custom_monthly_credits_usd is not None:
                org.custom_monthly_credits_usd = custom_monthly_credits_usd
            if custom_included_phone_numbers is not None:
                org.custom_included_phone_numbers = custom_included_phone_numbers
            if custom_byok_platform_fee_usd is not None:
                org.custom_byok_platform_fee_usd = custom_byok_platform_fee_usd
            if custom_allow_live_transfer is not None:
                org.custom_allow_live_transfer = custom_allow_live_transfer
            if custom_allow_sip_trunking is not None:
                org.custom_allow_sip_trunking = custom_allow_sip_trunking

            await session.commit()

        logger.info(
            "Updated plan for organization {} to tier={} (credits=${}, concurrency={}, agents={})",
            organization_id,
            plan_slug,
            monthly_credits,
            custom_concurrent_limit,
            custom_max_agents,
        )
        return await self.get_effective_limits(organization_id)

    async def record_run_billing(
        self,
        organization_id: int,
        duration_seconds: float,
        rate_per_min: float,
    ) -> Dict[str, Any]:
        """Allocate call cost between monthly subscription included credits and platform wallet overage."""
        limits = await self.get_effective_limits(organization_id)
        call_duration_minutes = duration_seconds / 60.0
        call_cost_usd = round(call_duration_minutes * rate_per_min, 4)

        charged_to = "wallet"
        plan_credits_deducted = 0.0
        wallet_cost_usd = 0.0
        new_wallet_balance = limits.wallet_balance_usd
        new_plan_credits = limits.plan_credits_remaining_usd

        if limits.plan_credits_remaining_usd > 0:
            if limits.plan_credits_remaining_usd >= call_cost_usd:
                # Fully covered under monthly plan credits!
                plan_credits_deducted = call_cost_usd
                wallet_cost_usd = 0.0
                new_plan_credits = round(limits.plan_credits_remaining_usd - plan_credits_deducted, 4)
                charged_to = "plan_credits"
            else:
                # Partially covered by plan credits, remainder charged to wallet!
                plan_credits_deducted = limits.plan_credits_remaining_usd
                wallet_cost_usd = round(call_cost_usd - plan_credits_deducted, 4)
                new_plan_credits = 0.0
                charged_to = "split"
        else:
            # Fully charged to wallet (Pay-As-You-Go or overage)
            plan_credits_deducted = 0.0
            wallet_cost_usd = call_cost_usd
            charged_to = "wallet"

        # 1. Update plan_credits_remaining_usd & monthly_minutes_used
        async with db_client.async_session() as session:
            stmt = (
                update(OrganizationModel)
                .where(OrganizationModel.id == organization_id)
                .values(
                    plan_credits_remaining_usd=new_plan_credits,
                    monthly_minutes_used=OrganizationModel.monthly_minutes_used + call_duration_minutes,
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
            "call_cost_usd": call_cost_usd,
            "plan_credits_deducted": plan_credits_deducted,
            "plan_credits_remaining": new_plan_credits,
            "wallet_cost_usd": wallet_cost_usd,
            "wallet_balance_after": new_wallet_balance,
            "effective_tier": limits.tier,
            "rate_per_minute": rate_per_min,
            "overage_rate_per_minute": limits.overage_rate_per_minute_usd,
        }


plan_service = PlanService()

