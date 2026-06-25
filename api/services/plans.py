"""Org plan tier + per-plan feature flags.

1 credit = 1 call-minute. An org's *plan tier* is the highest pack it has ever
paid for (Razorpay) — there is no plan column; the `payment_transactions` table
is the source of truth. Trial orgs (no successful purchase) sit below Starter
and get no paid features.

Feature gates (see CREDIT_PACKS[*]["features"] in api/constants.py):
  - api: REST API keys / Developers surface — Growth & Scale
  - mcp: MCP server — Scale only
"""

from typing import Iterable

from api.constants import CREDIT_PACKS

TRIAL_PLAN = "trial"

# Higher rank = more capable. Trial is the floor for orgs that never purchased.
PLAN_RANK = {TRIAL_PLAN: 0, "starter": 1, "growth": 2, "scale": 3}

_DEFAULT_FEATURES = {"api": False, "mcp": False}


def features_for_plan(plan: str) -> dict:
    """The feature flags for a plan tier. Trial / unknown tiers get nothing."""
    pack = next((p for p in CREDIT_PACKS if p["id"] == plan), None)
    feats = pack.get("features") if pack else None
    if isinstance(feats, dict):
        return {"api": bool(feats.get("api")), "mcp": bool(feats.get("mcp"))}
    return dict(_DEFAULT_FEATURES)


def plan_from_pack_ids(pack_ids: Iterable[str]) -> str:
    """Highest-ranked plan among the paid pack ids (default TRIAL_PLAN)."""
    best = TRIAL_PLAN
    for pid in pack_ids:
        if PLAN_RANK.get(pid, 0) > PLAN_RANK.get(best, 0):
            best = pid
    return best


async def get_org_plan(organization_id: int) -> str:
    """Resolve an org's plan tier from its successful purchases."""
    from api.db import db_client

    pack_ids = await db_client.get_paid_pack_ids(organization_id)
    return plan_from_pack_ids(pack_ids)
