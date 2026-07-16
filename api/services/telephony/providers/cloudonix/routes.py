"""Cloudonix telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json

from fastapi import APIRouter, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)

router = APIRouter()

# Cloudonix session statuses that terminate a transfer without an answer.
_CLOUDONIX_TRANSFER_FAILURE_STATUSES = {
    "busy",
    "noanswer",
    "cancel",
    "nocredit",
    "error",
    "congestion",
    "failed",
}


@router.post("/cloudonix/transfer-result/{transfer_id}")
async def handle_cloudonix_transfer_result(transfer_id: str, request: Request):
    """Drive transfer completion from the destination leg's session status.

    ``CloudonixProvider.transfer_call`` sets this URL as the outbound call
    object's ``callback``. Cloudonix POSTs session-status notifications here;
    a ``connected`` status means the destination answered (publish
    DESTINATION_ANSWERED so the shared handler forks the caller into the
    conference), while terminal non-answer statuses publish TRANSFER_FAILED.
    Intermediate statuses (ringing/processing) are acked without publishing.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        data = dict(await request.form())

    # Cloudonix session notifications may arrive as a single object or a
    # one-element list (see session-update webhook payloads).
    if isinstance(data, list):
        data = data[0] if data else {}

    conferenceStatus = str(data.get("StatusCallbackEvent", "")).lower()
    outboundCallStatus = str(data.get("status", "")).lower()
    destination_token = data.get("Session", "")

    logger.info(
        f"[Cloudonix Transfer] transfer_id={transfer_id} status={outboundCallStatus} conferenceStatus={conferenceStatus}"
        f"token={destination_token}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        logger.warning(
            f"[Cloudonix Transfer] No transfer context for {transfer_id}; ignoring"
        )
        return {"status": "ignored", "reason": "unknown_transfer"}

    original_call_sid = transfer_context.original_call_sid
    conference_name = transfer_context.conference_name

    if conferenceStatus == "participant-join":
        event = TransferEvent(
            type=TransferEventType.DESTINATION_ANSWERED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=destination_token,
            conference_name=conference_name,
            message="Great! The destination answered. Connecting you now.",
            status="success",
            action="destination_answered",
        )
    elif outboundCallStatus in _CLOUDONIX_TRANSFER_FAILURE_STATUSES:
        event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=destination_token,
            conference_name=conference_name,
            message="The transfer call could not be completed.",
            status="transfer_failed",
            action="transfer_failed",
            reason=outboundCallStatus,
        )
    else:
        logger.info(
            f"[Cloudonix Transfer] Intermediate status {outboundCallStatus} for {transfer_id}, "
            "waiting"
        )
        return {"status": "pending"}

    await call_transfer_manager.publish_transfer_event(event)
    return {"status": "completed"}


@router.post("/cloudonix/status-callback/{workflow_run_id}")
async def handle_cloudonix_status_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Cloudonix-specific status callbacks.

    Cloudonix sends call status updates to the callback URL specified during call initiation.
    """
    set_current_run_id(workflow_run_id)
    # Parse callback data - determine if JSON or form data
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        callback_data = await request.json()
    else:
        # Assume form data (like Twilio)
        form_data = await request.form()
        callback_data = dict(form_data)

    logger.info(
        f"[run {workflow_run_id}] Received Cloudonix status callback: {json.dumps(callback_data)}"
    )

    # Get workflow run to find organization
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for status callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

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

    return {"status": "success"}


@router.post("/cloudonix/cdr")
async def handle_cloudonix_cdr(request: Request):
    """Handle Cloudonix CDR (Call Detail Record) webhooks.

    Cloudonix sends CDR records when calls complete. The CDR contains:
    - domain: Used to identify the organization
    - call_id: Used to find the workflow run
    - disposition: Call termination status (ANSWER, BUSY, CANCEL, FAILED, CONGESTION, NOANSWER)
    - duration/billsec: Call duration information
    """
    try:
        cdr_data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Cloudonix CDR JSON: {e}")
        return {"status": "error", "message": "Invalid JSON payload"}

    # Extract domain to find organization
    domain = cdr_data.get("domain")
    if not domain:
        logger.warning("Cloudonix CDR missing domain field")
        return {"status": "error", "message": "Missing domain field"}

    # Extract call_id to find workflow run
    session = cdr_data.get("session")
    call_id = session.get("token") if isinstance(session, dict) else None
    logger.info(f"Cloudonix CDR data for call id {call_id} - {cdr_data}")
    if not call_id:
        logger.warning("Cloudonix CDR missing call_id field")
        return {"status": "error", "message": "Missing call_id field"}

    # Find workflow run by call_id in gathered_context
    workflow_run = await db_client.get_workflow_run_by_call_id(call_id)
    if not workflow_run:
        logger.warning(f"No workflow run found for Cloudonix call_id: {call_id}")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)
    logger.info(f"[run {workflow_run_id}] Processing Cloudonix CDR for call {call_id}")

    parsed_data = CloudonixProvider.parse_cdr_status_callback(cdr_data)
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    logger.info(
        f"[run {workflow_run_id}] Cloudonix CDR processed successfully - "
        f"disposition: {cdr_data.get('disposition')}, status: {status_update.status}"
    )

    return {"status": "success"}
