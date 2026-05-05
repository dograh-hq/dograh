"""Telnyx telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from typing import Optional

from fastapi import APIRouter, Header, Request
from loguru import logger

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.providers.telnyx.provider import (
    TelnyxProvider,
    normalize_event_type,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.common import get_backend_endpoints
from pipecat.utils.run_context import set_current_run_id

router = APIRouter()


@router.post("/telnyx/inbound/run")
async def handle_telnyx_inbound(request: Request):
    """Handle Telnyx inbound call webhooks.

    Telnyx sends all call lifecycle events for inbound calls to the
    ``webhook_event_url`` configured on the Call Control Application.
    This route accepts the initial ``call.initiated`` event and hands
    off to the shared ``/inbound/run`` dispatcher in
    ``api.routes.telephony`` for workflow resolution and streaming
    setup.
    """
    event_data = await request.json()
    logger.info(f"Telnyx inbound webhook: {json.dumps(event_data)}")

    # Normalize inbound payload
    normalized = TelnyxProvider.parse_inbound_webhook(event_data)
    logger.info(
        f"Telnyx inbound call from={normalized.from_number} "
        f"to={normalized.to_number} call_id={normalized.call_id}"
    )

    # Forward to shared dispatcher. The dispatcher will resolve the
    # workflow from the called number and handle signature verification.
    from api.routes.telephony import handle_inbound_run

    return await handle_inbound_run(request)


@router.post("/telnyx/events/{workflow_run_id}")
async def handle_telnyx_events(
    request: Request,
    workflow_run_id: int,
    x_telnyx_signature_ed25519: Optional[str] = Header(None),
    x_telnyx_timestamp: Optional[str] = Header(None),
    x_telnyx_public_key: Optional[str] = Header(None),
):
    """Handle Telnyx Call Control webhook events.

    Telnyx sends all call lifecycle events (call.initiated, call.answered,
    call.hangup, streaming.started, streaming.stopped) as JSON POST requests.
    """
    set_current_run_id(workflow_run_id)

    raw_body = await request.body()
    event_data = json.loads(raw_body)
    logger.info(
        f"[run {workflow_run_id}] Received Telnyx event: {json.dumps(event_data)}"
    )

    # Extract event type from Telnyx envelope. Telnyx sometimes delivers the
    # type with underscores (``streaming_started``) instead of dots
    # (``streaming.started``); normalize so downstream comparisons match either.
    data = event_data.get("data", {})
    event_type = normalize_event_type(data.get("event_type", ""))

    # Skip streaming events — they're informational only
    if event_type in ("streaming.started", "streaming.stopped"):
        logger.debug(f"[run {workflow_run_id}] Telnyx streaming event: {event_type}")
        return {"status": "success"}

    # Get workflow run and provider
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for Telnyx event")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    # Verify Ed25519 webhook signature if present
    if x_telnyx_signature_ed25519:
        backend_endpoint, _ = await get_backend_endpoints()
        full_url = (
            f"{backend_endpoint}/api/v1/telephony"
            f"/telnyx/events/{workflow_run_id}"
        )
        sig_params = {
            "telnyx_timestamp": x_telnyx_timestamp,
            "telnyx_public_key": x_telnyx_public_key,
            "_raw_body": raw_body.decode("utf-8"),
        }
        is_valid = await provider.verify_webhook_signature(
            full_url, sig_params, x_telnyx_signature_ed25519
        )
        if not is_valid:
            logger.warning(
                f"Invalid Telnyx Ed25519 signature for workflow run {workflow_run_id}"
            )
            return {"status": "error", "reason": "invalid_signature"}

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(event_data)

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
