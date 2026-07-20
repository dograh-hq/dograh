"""Plivo telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json

import aiohttp
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
from api.services.telephony.transfer_event_protocol import TransferEvent, TransferEventType
from api.services.telephony.call_transfer_manager import get_call_transfer_manager

router = APIRouter()


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

    workflow = await db_client.get_workflow_by_id(workflow_id)
    enable_dtmf = workflow.enable_dtmf if workflow else False

    response_content = await provider.get_webhook_response(
        workflow_id, organization_id, workflow_run_id, enable_dtmf
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


@router.post("/plivo/dtmf-callback/{workflow_run_id}")
async def handle_plivo_dtmf_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle DTMF events from Plivo."""
    form_data = await request.form()
    data = dict(form_data)
    digit = data.get("Digits")
    call_id = data.get("CallUUID") or data.get("RequestUUID")
    
    if call_id and digit:
        from api.services.telephony.dtmf_manager import dtmf_manager
        from api.services.telephony.dtmf_event_protocol import DTMFEvent
        from datetime import datetime, UTC
        
        event = DTMFEvent(
            call_id=call_id,
            digit=digit,
            timestamp=datetime.now(UTC),
        )
        await dtmf_manager.publish_dtmf_event(call_id, event)
        logger.info(f"Received Plivo DTMF digit: {digit} for call {call_id}")
        
    return HTMLResponse(content='<?xml version="1.0" encoding="UTF-8"?><Response/>', media_type="application/xml")



@router.post("/plivo/transfer-xml/{conference_name}/{transfer_id}", include_in_schema=False)
async def handle_plivo_transfer_xml(conference_name: str, transfer_id: str, request: Request):
    """
    Handle answer webhook from Plivo for transfer calls (destination/bleg).
    Returns Plivo XML to put the destination into the conference room, and
    publishes DESTINATION_ANSWERED so Dograh knows the call connected.
    """
    form_data = await request.form()
    data = dict(form_data)
    call_uuid = data.get("CallUUID", "")
    leg_type = request.query_params.get("leg", "bleg")

    logger.info(
        f"Plivo transfer answered (transfer_id={transfer_id}, leg={leg_type}): "
        f"CallUUID={call_uuid} - Bridging into conference {conference_name}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        return HTMLResponse(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>', media_type="application/xml")

    workflow_run_id = transfer_context.workflow_run_id
    from api.db import db_client
    from api.services.telephony.factory import get_telephony_provider_for_run
    import aiohttp
    from api.utils.common import get_backend_endpoints

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return HTMLResponse(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>', media_type="application/xml")

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    provider = await get_telephony_provider_for_run(workflow_run, workflow.organization_id)

    is_valid = await provider.verify_inbound_signature(
        str(request.url), data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(f"Invalid Plivo signature for transfer XML {transfer_id}")
        return HTMLResponse(content='<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>', media_type="application/xml")

    original_call_sid = transfer_context.original_call_sid

    if leg_type != "aleg":
        # Trigger Plivo API to redirect the original caller (aleg) into this conference.
        if workflow_run_id and original_call_sid:
            if hasattr(provider, "auth_id") and hasattr(provider, "auth_token"):
                    backend_endpoint, _ = await get_backend_endpoints()
                    # Add ?leg=aleg so we know it's the original caller and don't loop
                    aleg_answer_url = f"{backend_endpoint}/api/v1/telephony/plivo/transfer-xml/{conference_name}/{transfer_id}?leg=aleg"
                    
                    transfer_endpoint = f"{provider.base_url}/Call/{original_call_sid}/"
                    transfer_data = {
                        "legs": "aleg",
                        "aleg_url": aleg_answer_url,
                        "aleg_method": "POST",
                    }
                    logger.info(f"Triggering Plivo Transfer API to bridge original caller: {original_call_sid} into conference {conference_name}")
                    
                    # Fire and forget the bridge request so we can return XML to the bleg immediately
                    async def _bridge_aleg():
                        try:
                            async with aiohttp.ClientSession() as session:
                                auth = aiohttp.BasicAuth(provider.auth_id, provider.auth_token)
                                async with session.post(transfer_endpoint, json=transfer_data, auth=auth) as t_response:
                                    t_status = t_response.status
                                    t_text = await t_response.text()
                                    if t_status in [200, 201, 202]:
                                        logger.info(f"Original caller bridged into conference: status={t_status}")
                                        transfer_event = TransferEvent(
                                            type=TransferEventType.DESTINATION_ANSWERED,
                                            transfer_id=transfer_id,
                                            original_call_sid=original_call_sid,
                                            transfer_call_sid=call_uuid,
                                            conference_name=conference_name,
                                            status="success",
                                            action="destination_answered",
                                            message="Destination answered — bridging into conference.",
                                        )
                                        await call_transfer_manager.publish_transfer_event(transfer_event)
                                    else:
                                        logger.error(f"Failed to bridge original caller: status={t_status} body={t_text}")
                                        transfer_event = TransferEvent(
                                            type=TransferEventType.TRANSFER_FAILED,
                                            transfer_id=transfer_id,
                                            original_call_sid=original_call_sid,
                                            transfer_call_sid=call_uuid,
                                            conference_name=conference_name,
                                            status="transfer_failed",
                                            action="transfer_failed",
                                            reason="bridge_failed",
                                            message="Failed to bridge original caller into conference.",
                                        )
                                        await call_transfer_manager.publish_transfer_event(transfer_event)
                                        
                                # Explicitly stop the stream so Plivo is forced to execute the transfer XML immediately
                                stop_stream_endpoint = f"{provider.base_url}/Call/{original_call_sid}/Stream/"
                                async with session.delete(stop_stream_endpoint, auth=auth) as s_response:
                                    s_status = s_response.status
                                    s_text = await s_response.text()
                                    logger.info(f"Plivo Stop Stream API called: status={s_status} body={s_text}")
                        except Exception as e:
                            logger.error(f"Error bridging original caller: {e}")
                    
                    import asyncio
                    asyncio.create_task(_bridge_aleg())

    # Return XML to drop the leg into the conference
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>You have answered a transfer call. Connecting you now.</Speak>
    <Conference endConferenceOnExit="true">{conference_name}</Conference>
</Response>"""
    return HTMLResponse(content=xml, media_type="application/xml")


@router.post("/plivo/transfer-result/{transfer_id}", include_in_schema=False)
async def handle_plivo_transfer_result(transfer_id: str, request: Request):
    """
    Plivo hangup/ring callback for the outbound transfer (destination) leg.

    Plivo fires this when the destination call changes state (answered,
    no-answer, failed, busy, completed). We map those states to Dograh's
    transfer event protocol and publish via Redis so the waiting
    transfer_call_handler can proceed.
    """
    form_data = await request.form()
    data = dict(form_data)
    event = data.get("Event", "")
    call_uuid = data.get("CallUUID", "")
    hangup_cause = data.get("HangupCause", "")

    logger.info(
        f"Plivo transfer-result webhook (transfer_id={transfer_id}): "
        f"Event={event} CallUUID={call_uuid} HangupCause={hangup_cause}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        return {"status": "error", "reason": "invalid_transfer_id"}

    workflow_run_id = transfer_context.workflow_run_id
    from api.db import db_client
    from api.services.telephony.factory import get_telephony_provider_for_run
    
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return {"status": "error", "reason": "invalid_run"}
        
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    provider = await get_telephony_provider_for_run(workflow_run, workflow.organization_id)

    is_valid = await provider.verify_inbound_signature(
        str(request.url), data, dict(request.headers)
    )
    if not is_valid:
        logger.warning(f"Invalid Plivo signature for transfer result {transfer_id}")
        return {"status": "error", "reason": "invalid_signature"}

    original_call_sid = transfer_context.original_call_sid
    conference_name = transfer_context.conference_name

    # StartStream / ringing — not a final state, wait for more events
    if event in ("StartStream", "Initiated", "Ringing"):
        return {"status": "pending"}

    if event == "StartStream" or hangup_cause == "":
        # Intermediate, ignore
        return {"status": "pending"}

    # Answered: destination picked up → bridge original caller into conference
    # Plivo fires HangupCause="" while the call is live; the call being
    # answered is signalled by HangupCause being absent on the ring callback.
    # We detect "answered" by checking HangupCause USER_BUSY / NO_ANSWER / NORMAL_CLEARING.
    if hangup_cause == "USER_BUSY":
        # USER_BUSY means busy — treat as failed
        if hangup_cause == "USER_BUSY":
            transfer_event = TransferEvent(
                type=TransferEventType.TRANSFER_FAILED,
                transfer_id=transfer_id,
                original_call_sid=original_call_sid,
                transfer_call_sid=call_uuid,
                conference_name=conference_name,
                status="transfer_failed",
                action="transfer_failed",
                reason="busy",
                message="The transfer call encountered a busy signal.",
                end_call=True,
            )
        else:
            # Empty hangup cause on ring callback = still ringing, skip
            return {"status": "pending"}
    elif hangup_cause == "NORMAL_CLEARING":
        # Call answered and then the conference was exited (both legs done)
        # Pipeline already torn down — nothing to do
        await call_transfer_manager.remove_transfer_context(transfer_id)
        return {"status": "success"}
    elif hangup_cause in ("NO_ANSWER", "ORIGINATOR_CANCEL"):
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid,
            transfer_call_sid=call_uuid,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason="no_answer",
            message="The transfer call was not answered.",
            end_call=True,
        )
    else:
        # Any other hangup cause = failed
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid,
            transfer_call_sid=call_uuid,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason="call_failed",
            message=f"Transfer call failed: {hangup_cause}",
            end_call=True,
        )

    await call_transfer_manager.publish_transfer_event(transfer_event)
    return {"status": "success"}
