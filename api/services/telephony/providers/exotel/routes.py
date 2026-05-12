"""Exotel telephony routes.

Mounted under /api/v1/telephony by api.routes.telephony via importlib.
"""

import json
from typing import Optional

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
from api.utils.common import get_backend_endpoints

router = APIRouter()


# ---------------------------------------------------------------------------
# Answer webhook — called by Exotel when the outbound call is answered.
# Returns ExoML that opens the bidirectional Stream.
# ---------------------------------------------------------------------------


@router.post("/exotel-xml", include_in_schema=False)
async def handle_exotel_xml_webhook(
    workflow_id: int,
    user_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
):
    """
    Handle initial webhook from Exotel when an outbound call is answered.
    Returns ExoML <Response><Stream …/></Response>.
    """
    set_current_run_id(workflow_run_id)
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Exotel answer webhook: "
        f"{json.dumps(callback_data)}"
    )

    # Exotel sends CallSid in the answer webhook — persist it on the run so
    # handle_websocket can resolve it later.
    call_id = callback_data.get("CallSid")
    if call_id:
        gathered_context = dict(workflow_run.gathered_context or {})
        gathered_context["call_id"] = call_id
        await db_client.update_workflow_run(
            run_id=workflow_run_id, gathered_context=gathered_context
        )

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )
    return HTMLResponse(content=response_content, media_type="application/xml")


# ---------------------------------------------------------------------------
# Status callback — called by Exotel on call completion / state changes.
# ---------------------------------------------------------------------------


@router.post("/exotel/status-callback/{workflow_run_id}")
async def handle_exotel_status_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Exotel StatusCallback POST."""
    set_current_run_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Exotel status callback: "
        f"{json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Exotel status callback: workflow run not found"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(
            f"[run {workflow_run_id}] Exotel status callback: workflow not found"
        )
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

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
