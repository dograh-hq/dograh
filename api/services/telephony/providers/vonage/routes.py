"""Vonage telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run

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


async def _read_json_body(request: Request) -> tuple[dict, str]:
    body_bytes = await request.body()
    try:
        raw_body = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Webhook body is not valid UTF-8"
        ) from exc
    try:
        return json.loads(raw_body or "{}"), raw_body
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook body is not JSON") from exc


async def _handle_vonage_event_request(request: Request, workflow_run_id: int):
    set_current_run_id(workflow_run_id)
    event_data, raw_body = await _read_json_body(request)
    logger.info(
        f"[run {workflow_run_id}] Received Vonage event "
        f"uuid={event_data.get('uuid')} status={event_data.get('status')}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.error(f"[run {workflow_run_id}] Workflow run not found")
        return {"status": "error", "message": "Workflow run not found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.error(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "error", "message": "Workflow not found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    signature_valid = await provider.verify_inbound_signature(
        str(request.url), event_data, dict(request.headers), raw_body
    )
    if not signature_valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    from api.services.telephony.status_processor import (
        StatusCallbackRequest,
        _process_status_update,
    )

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
    return {"status": "ok"}


@router.post("/vonage/events/{workflow_run_id}")
async def handle_vonage_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Vonage-specific event webhooks.

    Vonage sends all call events to a single endpoint.
    Events include: started, ringing, answered, complete, failed, etc.
    """
    return await _handle_vonage_event_request(request, workflow_run_id)


@router.post("/vonage/events")
async def handle_vonage_events_without_run(request: Request):
    """Handle application-level events by resolving the run from call UUID."""
    event_data, _ = await _read_json_body(request)
    call_id = event_data.get("uuid")
    if call_id:
        workflow_run = await db_client.get_workflow_run_by_call_id(call_id)
        if workflow_run:
            return await _handle_vonage_event_request(request, workflow_run.id)

    logger.info(
        "Received unmatched Vonage application event "
        f"uuid={event_data.get('uuid')} status={event_data.get('status')}"
    )
    return {"status": "ok"}
