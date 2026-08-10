"""Plivo telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import HTMLResponse

from api.db import db_client
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)

router = APIRouter()


def _hangup_xml_response() -> HTMLResponse:
    return HTMLResponse(
        content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>',
        media_type="application/xml",
    )


def _conference_xml_response(conference_name: str) -> HTMLResponse:
    # Plivo joins a call to a standard conference through Conference XML;
    # its Conference REST API only manages participants already in the conference.
    # https://docs.plivo.com/docs/voice/xml/conference
    safe_conference_name = escape(conference_name)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>You have answered a transfer call. Connecting you now.</Speak>
    <Conference endConferenceOnExit="true">{safe_conference_name}</Conference>
</Response>"""
    return HTMLResponse(content=xml, media_type="application/xml")


async def _handle_plivo_status_callback(
    workflow_run_id: int,
    request: Request,
):
    set_current_run_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Received Plivo callback: {json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for Plivo callback")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
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
        logger.warning(f"[run {workflow_run_id}] Invalid Plivo webhook signature")
        return {"status": "error", "reason": "invalid_signature"}

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


@router.post("/plivo-xml", include_in_schema=False)
async def handle_plivo_xml_webhook(
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
):
    """
    Handle initial webhook from Plivo when an outbound call is answered.
    Returns Plivo XML response with Stream element.
    """
    set_current_run_id(workflow_run_id)
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    form_data = await request.form()
    callback_data = dict(form_data)

    is_valid = await provider.verify_inbound_signature(
        str(request.url), callback_data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid Plivo signature on answer webhook"
        )
        return provider.generate_error_response(
            "invalid_signature", "Invalid webhook signature."
        )

    call_id = callback_data.get("CallUUID") or callback_data.get("RequestUUID")
    if call_id:
        gathered_context = dict(workflow_run.gathered_context or {})
        gathered_context["call_id"] = call_id
        await db_client.update_workflow_run(
            run_id=workflow_run_id, gathered_context=gathered_context
        )

    response_content = await provider.get_webhook_response(
        workflow_id, organization_id, workflow_run_id
    )
    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/plivo/hangup-callback/{workflow_run_id}")
async def handle_plivo_hangup_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Plivo hangup callbacks."""
    return await _handle_plivo_status_callback(workflow_run_id, request)


@router.post("/plivo/ring-callback/{workflow_run_id}")
async def handle_plivo_ring_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Plivo ring callbacks."""
    return await _handle_plivo_status_callback(workflow_run_id, request)


@router.post(
    "/plivo/transfer-xml/{conference_name}/{transfer_id}", include_in_schema=False
)
async def handle_plivo_transfer_xml(
    conference_name: str, transfer_id: str, request: Request
):
    """
    Return conference XML for either leg of a Plivo transfer.

    The destination callback publishes ``DESTINATION_ANSWERED`` after placing
    that leg in the conference. Pipeline teardown then invokes the Plivo
    transfer strategy, which redirects the original caller back to this XML.
    """
    form_data = await request.form()
    data = dict(form_data)
    callback_call_uuid = data.get("CallUUID", "")
    is_original_leg = request.query_params.get("leg") == "aleg"
    leg_name = "original" if is_original_leg else "destination"

    logger.info(
        f"Plivo transfer XML requested (transfer_id={transfer_id}, leg={leg_name}): "
        f"CallUUID={callback_call_uuid} conference={conference_name}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        return _hangup_xml_response()

    if conference_name != transfer_context.conference_name:
        logger.warning(
            f"Conference mismatch for Plivo transfer {transfer_id}: "
            f"requested={conference_name} expected={transfer_context.conference_name}"
        )
        return _hangup_xml_response()

    workflow_run_id = transfer_context.workflow_run_id

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return _hangup_xml_response()

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        return _hangup_xml_response()
    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    is_valid = await provider.verify_inbound_signature(
        str(request.url), data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(f"Invalid Plivo signature for transfer XML {transfer_id}")
        return _hangup_xml_response()

    if not is_original_leg:
        # The Make Call response contains a RequestUUID; the answer callback
        # is the first place Plivo provides the destination's actual CallUUID.
        destination_call_uuid = callback_call_uuid
        if destination_call_uuid and transfer_context.call_sid != destination_call_uuid:
            transfer_context.call_sid = destination_call_uuid
            await call_transfer_manager.store_transfer_context(transfer_context)

        if await call_transfer_manager.claim_transfer_step(
            transfer_id, "destination_answered"
        ):
            await call_transfer_manager.publish_transfer_event(
                TransferEvent(
                    type=TransferEventType.DESTINATION_ANSWERED,
                    transfer_id=transfer_id,
                    original_call_sid=transfer_context.original_call_sid,
                    transfer_call_sid=destination_call_uuid,
                    conference_name=transfer_context.conference_name,
                    status="success",
                    action="destination_answered",
                    message="Destination answered — bridging into conference.",
                )
            )

    return _conference_xml_response(transfer_context.conference_name)


@router.post("/plivo/transfer-result/{transfer_id}", include_in_schema=False)
async def handle_plivo_transfer_result(transfer_id: str, request: Request):
    """
    Plivo hangup callback for the outbound transfer destination.

    Destination answer is handled by ``handle_plivo_transfer_xml``. This route
    publishes terminal failures and cleans up normal completion.
    """
    form_data = await request.form()
    data = dict(form_data)
    event = data.get("Event", "")
    destination_call_uuid = data.get("CallUUID", "")
    hangup_cause = data.get("HangupCause", "")

    logger.info(
        f"Plivo transfer-result webhook (transfer_id={transfer_id}): "
        f"Event={event} CallUUID={destination_call_uuid} HangupCause={hangup_cause}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        return {"status": "error", "reason": "invalid_transfer_id"}

    workflow_run_id = transfer_context.workflow_run_id

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return {"status": "error", "reason": "invalid_run"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        return {"status": "error", "reason": "invalid_workflow"}
    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    is_valid = await provider.verify_inbound_signature(
        str(request.url), data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(f"Invalid Plivo signature for transfer result {transfer_id}")
        return {"status": "error", "reason": "invalid_signature"}

    original_call_uuid = transfer_context.original_call_sid
    conference_name = transfer_context.conference_name

    if not hangup_cause:
        return {"status": "pending"}

    if hangup_cause == "USER_BUSY":
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_uuid,
            transfer_call_sid=destination_call_uuid,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason="busy",
            message="The transfer call encountered a busy signal.",
        )
    elif hangup_cause == "NORMAL_CLEARING":
        # Call answered and then the conference was exited (both legs done)
        # Pipeline already torn down — nothing to do
        await call_transfer_manager.remove_transfer_context(transfer_id)
        return {"status": "success"}
    elif hangup_cause in ("NO_ANSWER", "ORIGINATOR_CANCEL"):
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_uuid,
            transfer_call_sid=destination_call_uuid,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason="no_answer",
            message="The transfer call was not answered.",
        )
    else:
        # Any other hangup cause = failed
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_uuid,
            transfer_call_sid=destination_call_uuid,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason="call_failed",
            message=f"Transfer call failed: {hangup_cause}",
        )

    if not await call_transfer_manager.claim_transfer_step(
        transfer_id, "failure_reported"
    ):
        return {"status": "success"}

    await call_transfer_manager.publish_transfer_event(transfer_event)
    await call_transfer_manager.remove_transfer_context(transfer_id)
    return {"status": "success"}
