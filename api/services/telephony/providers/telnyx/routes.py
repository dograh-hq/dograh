"""Telnyx telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from loguru import logger
from starlette.responses import HTMLResponse

from api.db import db_client
from api.errors.telephony_errors import TelephonyError
from api.services.telephony.factory import get_telephony_provider
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_helper import (
    generic_hangup_response,
    normalize_webhook_data,
    parse_webhook_request,
)
from pipecat.utils.run_context import set_current_run_id

router = APIRouter()


@router.post("/telnyx/events/{workflow_run_id}")
async def handle_telnyx_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Telnyx Call Control webhook events.

    Telnyx sends all call lifecycle events (call.initiated, call.answered,
    call.hangup, streaming.started, streaming.stopped) as JSON POST requests.
    """
    set_current_run_id(workflow_run_id)

    event_data = await request.json()
    logger.info(
        f"[run {workflow_run_id}] Received Telnyx event: {json.dumps(event_data)}"
    )

    # Extract event type from Telnyx envelope
    data = event_data.get("data", {})
    event_type = data.get("event_type", "")

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

    provider = await get_telephony_provider(workflow.organization_id)

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
