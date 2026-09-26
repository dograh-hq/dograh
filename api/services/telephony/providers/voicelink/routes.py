"""VoiceLink telephony routes.

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry.
"""

import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


async def _parse_callback_body(request: Request) -> dict:
    # VoiceLink posts Call Event webhooks as JSON only.
    try:
        data = await request.json()
    except Exception as e:
        logger.warning(f"VoiceLink status callback body parse failed: {e}")
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/voicelink/status-callback/{workflow_run_id}")
async def handle_voicelink_status_callback(workflow_run_id: int, request: Request):
    """Call Event webhook for one outbound run, URL minted by ``initiate_call``.

    Covers calls that fail or go unanswered before the media socket opens,
    which would otherwise leave the run queued.
    """
    set_current_run_id(workflow_run_id)

    callback_data = await _parse_callback_body(request)
    if not callback_data:
        return {"status": "ignored", "reason": "malformed_body"}

    logger.info(
        f"[run {workflow_run_id}] VoiceLink status callback: "
        f"{json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    is_valid = await provider.verify_inbound_signature(
        str(request.url),
        callback_data,
        dict(request.headers),
    )
    if not is_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid VoiceLink status callback auth"
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    parsed = provider.parse_status_callback(callback_data)
    expected_call_id = None
    gathered = workflow_run.gathered_context or {}
    if isinstance(gathered, dict):
        expected_call_id = gathered.get("call_id")
    presented = parsed.get("call_id") or ""
    # Fail closed: the webhook's outboundQueueId must match the call id
    # initiate_call recorded for this run.
    if not expected_call_id or not presented:
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink status missing call id binding "
            f"expected={expected_call_id!r} got={presented!r}"
        )
        raise HTTPException(status_code=403, detail="Call id binding required")
    try:
        bound = hmac.compare_digest(
            str(expected_call_id).encode("utf-8"),
            str(presented).encode("utf-8"),
        )
    except (TypeError, UnicodeError):
        bound = False
    if not bound:
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink status call id mismatch "
            f"expected={expected_call_id!r} got={presented!r}"
        )
        raise HTTPException(status_code=403, detail="Call id mismatch")

    await _process_status_update(
        workflow_run_id,
        StatusCallbackRequest(
            call_id=parsed["call_id"],
            status=parsed["status"],
            from_number=parsed.get("from_number"),
            to_number=parsed.get("to_number"),
            direction=parsed.get("direction"),
            duration=parsed.get("duration"),
            extra=parsed.get("extra", {}),
        ),
    )
    return {"status": "success"}
