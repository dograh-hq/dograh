"""VoiceLink KYC routes.

Thin handlers over ``api.services.voicelink_kyc``: resolve the org's
optional VoiceLink ``client_id`` (stored on the org's VoiceLink telephony
configuration credentials), forward the step payload to the VoiceLink
reseller KYC API, and map upstream failures to 502. When the reseller
credentials are unset in the environment, ``GET /status`` reports
``{"enabled": false}`` and step endpoints return 503.
"""

from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db.models import UserModel
from api.schemas.kyc import (
    KycActionResponse,
    KycStatusResponse,
    KycStep1Request,
    KycStep2Request,
    KycStep3Request,
    KycStep4Request,
)
from api.services.auth.depends import get_user
from api.services.voicelink_kyc import (
    VoiceLinkKycClient,
    VoiceLinkKycError,
    get_kyc_client,
    resolve_org_voicelink_client_id,
)

router = APIRouter(prefix="/kyc", tags=["kyc"])


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


def _require_configured_client() -> VoiceLinkKycClient:
    client = get_kyc_client()
    if not client.is_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "VoiceLink KYC is not configured — set "
                "VOICELINK_RESELLER_USERNAME and VOICELINK_RESELLER_PASSWORD"
            ),
        )
    return client


async def _resolve_client_id(user: UserModel) -> Tuple[Optional[str], bool]:
    organization_id = _organization_id(user)
    return await resolve_org_voicelink_client_id(organization_id)


def _action_response(envelope: Dict[str, Any]) -> KycActionResponse:
    return KycActionResponse(
        message=envelope.get("message"),
        data=envelope.get("data") or {},
    )


@router.get("/status", response_model=KycStatusResponse)
async def get_kyc_status(user: UserModel = Depends(get_user)):
    """KYC status for the caller's organization."""
    client = get_kyc_client()
    if not client.is_configured:
        return KycStatusResponse(enabled=False)

    client_id, has_voicelink_config = await _resolve_client_id(user)
    try:
        envelope = await client.get_status(client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))

    data = envelope.get("data") or {}
    return KycStatusResponse(
        enabled=True,
        client_id_configured=bool(client_id),
        has_voicelink_config=has_voicelink_config,
        kyc_status=data.get("kyc_status"),
        pan_verified=data.get("pan_verified"),
        aadhaar_verified=data.get("aadhaar_verified"),
        gst_verified=data.get("gst_verified"),
        is_complete=data.get("is_complete"),
        current_step=data.get("current_step"),
        account_type=data.get("account_type"),
    )


@router.post("/step-1", response_model=KycActionResponse)
async def kyc_step_1_register_details(
    request: KycStep1Request, user: UserModel = Depends(get_user)
):
    """Step 1 — register account details."""
    client = _require_configured_client()
    client_id, _ = await _resolve_client_id(user)
    try:
        envelope = await client.step1_register_details(
            request.model_dump(exclude_none=True), client_id
        )
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"VoiceLink KYC step 1 submitted (client_id={client_id})")
    return _action_response(envelope)


@router.post("/step-2", response_model=KycActionResponse)
async def kyc_step_2_pan_verify(
    request: KycStep2Request, user: UserModel = Depends(get_user)
):
    """Step 2 — PAN verification."""
    client = _require_configured_client()
    client_id, _ = await _resolve_client_id(user)
    try:
        envelope = await client.step2_pan_verify(request.model_dump(), client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"VoiceLink KYC step 2 submitted (client_id={client_id})")
    return _action_response(envelope)


@router.post("/step-3", response_model=KycActionResponse)
async def kyc_step_3_aadhaar_init(
    request: KycStep3Request, user: UserModel = Depends(get_user)
):
    """Step 3 — initiate Aadhaar verification; returns a DigiLocker redirect_url."""
    client = _require_configured_client()
    client_id, _ = await _resolve_client_id(user)
    try:
        envelope = await client.step3_aadhaar_init(request.redirect_url, client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"VoiceLink KYC step 3 (Aadhaar init) submitted (client_id={client_id})")
    return _action_response(envelope)


@router.post("/step-4", response_model=KycActionResponse)
async def kyc_step_4_gst_verify(
    request: KycStep4Request, user: UserModel = Depends(get_user)
):
    """Step 4 — GST verification (business accounts only)."""
    client = _require_configured_client()
    client_id, _ = await _resolve_client_id(user)
    try:
        envelope = await client.step4_gst_verify(request.model_dump(), client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"VoiceLink KYC step 4 submitted (client_id={client_id})")
    return _action_response(envelope)


@router.post("/final-submit", response_model=KycActionResponse)
async def kyc_final_submit(user: UserModel = Depends(get_user)):
    """Submit the completed KYC for review."""
    client = _require_configured_client()
    client_id, _ = await _resolve_client_id(user)
    try:
        envelope = await client.final_submit(client_id)
    except VoiceLinkKycError as e:
        raise HTTPException(status_code=502, detail=str(e))
    logger.info(f"VoiceLink KYC final submit (client_id={client_id})")
    return _action_response(envelope)
