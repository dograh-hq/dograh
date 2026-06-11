"""VoiceLink telephony routes (call-event webhooks).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json

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


@router.post("/voicelink/events/{workflow_run_id}")
async def handle_voicelink_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle VoiceLink call-event webhooks.

    VoiceLink POSTs nested camelCase JSON for every call lifecycle event
    (call.initiated, call.ringing, call.answered, call.completed,
    call.ended, call.failed) to the ``webhook_url`` passed in ``add_lead``.
    VoiceLink expects a 200 for valid events — webhooks are unsigned, so
    no signature verification is possible.
    """
    set_current_run_id(workflow_run_id)

    try:
        event_data = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink event body is not valid JSON: {e}"
        )
        return {"status": "error", "reason": "invalid_json"}

    event_type = event_data.get("event", "")
    logger.info(
        f"[run {workflow_run_id}] Received VoiceLink event: event={event_type}"
    )
    logger.debug(
        f"[run {workflow_run_id}] VoiceLink event body: {json.dumps(event_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for VoiceLink event"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    # Parse the nested event into the generic format
    parsed_data = provider.parse_status_callback(event_data)

    logger.debug(
        f"[run {workflow_run_id}] Parsed VoiceLink event: "
        f"call_id={parsed_data['call_id']}, status={parsed_data['status']}"
    )

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

    logger.info(
        f"[run {workflow_run_id}] VoiceLink event {event_type} processed successfully"
    )

    return {"status": "success"}
