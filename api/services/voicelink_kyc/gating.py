"""Gate outbound calling on VoiceLink KYC completion.

Outbound (campaign start/resume, public API trigger) must not run until the org's
VoiceLink KYC is complete. Design choices:
- Gate at campaign START/RESUME + public trigger (NOT per-dial) — one status check,
  no per-call latency or external dependency on the hot dialing path.
- KYC not configured in the deployment -> ALLOWED (this deployment doesn't use KYC).
- Org has no VoiceLink client_id -> ALLOWED (it dials via the shared reseller account,
  whose KYC the reseller owns; or telephony isn't set up and fails downstream anyway).
- is_complete True -> ALLOWED; False -> BLOCKED (403).
- VoiceLink status API errors -> FAIL-OPEN with a warning, so a reseller outage can't
  halt all calling (VoiceLink also enforces KYC downstream).
"""

from __future__ import annotations

from fastapi import HTTPException
from loguru import logger

from api.services.voicelink_kyc import (
    VoiceLinkKycError,
    get_kyc_client,
    resolve_org_voicelink_client_id,
)

KYC_INCOMPLETE_MESSAGE = (
    "Complete your KYC verification before starting outbound calls."
)


async def is_org_kyc_complete(organization_id: int) -> bool:
    """True if the org may dial outbound (see module docstring for the gate rules)."""
    client = get_kyc_client()
    if not client.is_configured:
        return True
    client_id, _ = await resolve_org_voicelink_client_id(organization_id)
    if not client_id:
        return True
    try:
        envelope = await client.get_status(client_id)
    except VoiceLinkKycError as exc:
        logger.warning(
            f"KYC status check failed for org {organization_id}; allowing (fail-open): {exc}"
        )
        return True
    data = (envelope or {}).get("data") or {}
    return bool(data.get("is_complete"))


async def assert_org_kyc_complete(organization_id: int) -> None:
    """Raise 403 if the org's KYC isn't complete; no-op otherwise."""
    if not await is_org_kyc_complete(organization_id):
        raise HTTPException(status_code=403, detail=KYC_INCOMPLETE_MESSAGE)
