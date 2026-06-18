"""Vonage telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


@router.get("/ncco", include_in_schema=False)
async def handle_ncco_webhook(
    workflow_id: int,
    user_id: int,
    workflow_run_id: int,
    organization_id: Optional[int] = None,
):
    """Handle NCCO (Nexmo Call Control Objects) webhook for Vonage.

    Returns JSON response instead of XML like TwiML.
    """

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(
        workflow_run, organization_id or user_id
    )

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    return json.loads(response_content)


@router.post("/vonage/events/{workflow_run_id}")
async def handle_vonage_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Vonage-specific event webhooks.

    Vonage sends all call events to a single endpoint.
    Events include: started, ringing, answered, complete, failed, etc.
    """
    set_current_run_id(workflow_run_id)
    # Parse the event data
    event_data = await request.json()
    logger.info(f"[run {workflow_run_id}] Received Vonage event: {event_data}")

    # Get workflow run for processing
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.error(f"[run {workflow_run_id}] Workflow run not found")
        return {"status": "error", "message": "Workflow run not found"}

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.error(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "error", "message": "Workflow not found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    # Parse the event data into generic format
    parsed_data = provider.parse_status_callback(event_data)

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    # Return 204 No Content as expected by Vonage
    return {"status": "ok"}


# Vonage raw statuses that mean "the human leg will not be answered by a person"
# — hand the caller to the AI.
_SCREENING_NO_ANSWER_STATUSES = {
    "complete",
    "timeout",
    "unanswered",
    "failed",
    "rejected",
    "busy",
    "cancelled",
}


@router.post("/vonage/smart-voicemail/events/{workflow_run_id}")
async def handle_vonage_smart_voicemail_events(
    request: Request,
    workflow_run_id: int,
):
    """Event webhook for the smart-voicemail *screening* leg (human number).

    Drives the screen-and-forward decision from leg state. Voicemail-vs-human is
    detected by our own pipeline (the listen-only screening websocket); this
    route only handles the call-state signals the pipeline can't see:
    ``answered`` (arm the silent-pickup watchdog) and the no-answer terminals.
    The orchestrator's latch makes whichever signal lands first authoritative.
    """
    from api.services.telephony.smart_voicemail import (
        get_smart_voicemail_orchestrator,
    )

    set_current_run_id(workflow_run_id)
    event_data = await request.json()
    status = (event_data or {}).get("status", "")
    logger.info(
        f"[run {workflow_run_id}] smart-voicemail screening event: {status}"
    )

    orchestrator = get_smart_voicemail_orchestrator()
    if status == "answered":
        await orchestrator.on_screening_answered(workflow_run_id)
    elif status in _SCREENING_NO_ANSWER_STATUSES:
        await orchestrator.on_screening_result(workflow_run_id, "no_answer")

    return {"status": "ok"}
