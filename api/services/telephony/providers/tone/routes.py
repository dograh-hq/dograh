"""Tone telephony routes (webhooks, status callbacks).

Mounted under /api/v1/telephony by api.routes.telephony via the
provider registry — see ProviderSpec.router.

Exotel sends:
  - Passthru applet callbacks: form-urlencoded with CallSid, Status, etc.
  - No HMAC signature — authentication is via IP whitelisting or Basic Auth
    embedded in the callback URL.
"""

import json

from fastapi import APIRouter, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import HTMLResponse

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


async def _handle_tone_status_callback(workflow_run_id: int, request: Request):
    set_current_run_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Received Tone callback: {json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for Tone callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(workflow_run, workflow.organization_id)

    # Exotel has no HMAC signature — verify_inbound_signature is a pass-through
    parsed_data = provider.parse_status_callback(callback_data)
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    await _process_status_update(workflow_run_id, status_update)
    return {"status": "success"}


@router.post("/tone-webhook", include_in_schema=False)
async def handle_tone_webhook(
    workflow_id: int,
    user_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
):
    """
    Handle initial webhook from Tone/Exotel when an outbound call is answered.

    Tone does not use TwiML or Plivo XML. The WebSocket URL is configured
    statically in the Exotel App Bazaar Voicebot Applet. This endpoint is used
    as the webhookUrl in POST /v1/calls for status callbacks only.
    """
    set_current_run_id(workflow_run_id)
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)

    # Store Exotel CallSid so handle_websocket can find it
    call_id = callback_data.get("CallSid") or callback_data.get("id", "")
    if call_id and workflow_run:
        gathered_context = dict(workflow_run.gathered_context or {})
        gathered_context["call_id"] = call_id
        await db_client.update_workflow_run(
            run_id=workflow_run_id, gathered_context=gathered_context
        )

    # No XML response needed — Exotel doesn't parse a webhook response for call control
    return {"status": "ok"}


@router.post("/tone/hangup-callback/{workflow_run_id}")
async def handle_tone_hangup_callback(workflow_run_id: int, request: Request):
    """Handle Tone/Exotel Passthru applet hangup callbacks."""
    return await _handle_tone_status_callback(workflow_run_id, request)


@router.post("/tone/ring-callback/{workflow_run_id}")
async def handle_tone_ring_callback(workflow_run_id: int, request: Request):
    """Handle Tone/Exotel ring callbacks."""
    return await _handle_tone_status_callback(workflow_run_id, request)
