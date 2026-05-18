"""Vobiz telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Header, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import HTMLResponse

from api.db import db_client
from api.services.telephony.factory import (
    get_telephony_provider_for_run,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_helper import (
    parse_webhook_request,
)

router = APIRouter()


@router.post("/vobiz-xml", include_in_schema=False)
async def handle_vobiz_xml_webhook(
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int
):
    """
    Handle initial webhook from Vobiz when call is answered.
    Returns Vobiz XML response with Stream element.

    Vobiz uses Plivo-compatible XML format similar to Twilio's TwiML.
    """
    set_current_run_id(workflow_run_id)
    logger.info(
        f"[run {workflow_run_id}] Vobiz XML webhook called - "
        f"workflow_id={workflow_id}, user_id={user_id}, org_id={organization_id}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    logger.debug(f"[run {workflow_run_id}] Using provider: {provider.PROVIDER_NAME}")

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    logger.debug(
        f"[run {workflow_run_id}] Vobiz XML response generated:\n{response_content}"
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/vobiz/hangup-callback/{workflow_run_id}")
async def handle_vobiz_hangup_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback (sent when call ends).

    Vobiz sends callbacks to hangup_url when the call terminates.
    This includes call duration, status, and billing information.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data (Vobiz sends form data or JSON)
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Body: {json.dumps(callback_data)}"
    )
    logger.info(
        f"[run {workflow_run_id}] Received Vobiz hangup callback {json.dumps(callback_data)}"
    )

    # Verify signature if provided
    if x_vobiz_signature:
        # We need the workflow run to get organization for provider credentials
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.warning(
                f"[run {workflow_run_id}] Workflow run not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_run_not_found"}

        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if not workflow:
            logger.warning(
                f"[run {workflow_run_id}] Workflow not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_not_found"}

        provider = await get_telephony_provider_for_run(
            workflow_run, workflow.organization_id
        )

        # Get raw body for signature verification
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")

        # Verify signature
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}"

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[run {workflow_run_id}] Invalid Vobiz hangup callback signature"
            )
            return {"status": "error", "reason": "invalid_signature"}

        logger.info(f"[run {workflow_run_id}] Vobiz hangup callback signature verified")
    else:
        # Get workflow run for processing (signature verification already got it if needed)
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for Vobiz hangup callback"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Get workflow and provider
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"[run {workflow_run_id}] Workflow not found")
        return {"status": "ignored", "reason": "workflow_not_found"}

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    logger.debug(
        f"[run {workflow_run_id}] Processing Vobiz hangup with provider: {provider.PROVIDER_NAME}"
    )

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

    logger.debug(
        f"[run {workflow_run_id}] Parsed Vobiz callback data: {json.dumps(parsed_data)}"
    )

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

    logger.info(f"[run {workflow_run_id}] Vobiz hangup callback processed successfully")

    return {"status": "success"}


@router.post("/vobiz/ring-callback/{workflow_run_id}")
async def handle_vobiz_ring_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz ring callback (sent when call starts ringing).

    Vobiz can send callbacks to ring_url when the call starts ringing.
    This is optional and used for tracking ringing status.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Body: {json.dumps(callback_data)}"
    )

    logger.info(
        f"[run {workflow_run_id}] Received Vobiz ring callback {json.dumps(callback_data)}"
    )

    # Verify signature if provided
    if x_vobiz_signature:
        # We need the workflow run to get organization for provider credentials
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            logger.warning(
                f"[run {workflow_run_id}] Workflow run not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_run_not_found"}

        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if not workflow:
            logger.warning(
                f"[run {workflow_run_id}] Workflow not found for signature verification"
            )
            return {"status": "error", "reason": "workflow_not_found"}

        provider = await get_telephony_provider_for_run(
            workflow_run, workflow.organization_id
        )

        # Get raw body for signature verification
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")

        # Verify signature
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = (
            f"{backend_endpoint}/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}"
        )

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[run {workflow_run_id}] Invalid Vobiz ring callback signature"
            )
            return {"status": "error", "reason": "invalid_signature"}

        logger.info(f"[run {workflow_run_id}] Vobiz ring callback signature verified")
    else:
        # Get workflow run for processing (signature verification already got it if needed)
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for Vobiz ring callback"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    # Log the ringing event
    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    ring_log = {
        "status": "ringing",
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": callback_data.get("call_uuid", callback_data.get("CallUUID", "")),
        "event_type": "ring",
        "raw_data": callback_data,
    }
    telephony_callback_logs.append(ring_log)

    # Update workflow run logs
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    logger.info(f"[run {workflow_run_id}] Vobiz ring callback logged")

    return {"status": "success"}


@router.post("/vobiz/hangup-callback/workflow/{workflow_id}")
async def handle_vobiz_hangup_callback_by_workflow(
    workflow_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback with workflow_id - finds workflow run by call_id."""

    all_headers = dict(request.headers)
    logger.info(
        f"[workflow {workflow_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    try:
        callback_data, _ = await parse_webhook_request(request)
    except ValueError:
        callback_data = {}

    call_uuid = callback_data.get("CallUUID") or callback_data.get("call_uuid")
    logger.info(
        f"[workflow {workflow_id}] Received Vobiz hangup callback for call {call_uuid}: {json.dumps(callback_data)}"
    )

    if not call_uuid:
        logger.warning(
            f"[workflow {workflow_id}] No call_uuid found in Vobiz hangup callback"
        )
        return {"status": "error", "message": "No call_uuid found"}

    workflow = await db_client.get_workflow_by_id(workflow_id)
    if not workflow:
        logger.warning(f"[workflow {workflow_id}] Workflow not found")
        return {"status": "error", "message": "workflow_not_found"}

    try:
        workflow_run = await db_client.get_workflow_run_by_call_id(call_uuid)
    except Exception as e:
        logger.error(
            f"[workflow {workflow_id}] Error finding workflow run for call {call_uuid}: {e}"
        )
        return {"status": "error", "message": str(e)}

    if not workflow_run or workflow_run.workflow_id != workflow_id:
        logger.warning(
            f"[workflow {workflow_id}] No workflow run found for call {call_uuid}"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)
    logger.info(
        f"[workflow {workflow_id}] Found workflow run {workflow_run_id} for call {call_uuid}"
    )

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    if x_vobiz_signature:
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")
        backend_endpoint, _ = await get_backend_endpoints()
        webhook_url = f"{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/workflow/{workflow_id}"

        is_valid = await provider.verify_webhook_signature(
            webhook_url,
            callback_data,
            x_vobiz_signature,
            x_vobiz_timestamp,
            webhook_body,
        )

        if not is_valid:
            logger.warning(
                f"[workflow {workflow_id}] Invalid Vobiz hangup callback signature"
            )
            return {"status": "error", "message": "invalid_signature"}

        logger.info(
            f"[workflow {workflow_id}] Vobiz hangup callback signature verified"
        )

    try:
        parsed_data = provider.parse_status_callback(callback_data)

        status = StatusCallbackRequest(
            call_id=parsed_data["call_id"],
            status=parsed_data["status"],
            from_number=parsed_data.get("from_number"),
            to_number=parsed_data.get("to_number"),
            direction=parsed_data.get("direction"),
            duration=parsed_data.get("duration"),
            extra=parsed_data.get("extra", {}),
        )

        await _process_status_update(workflow_run_id, status)

        logger.info(
            f"[run {workflow_run_id}] Vobiz hangup callback processed successfully"
        )
        return {"status": "success"}

    except Exception as e:
        logger.error(
            f"[run {workflow_run_id}] Error processing Vobiz hangup callback: {e}"
        )
        return {"status": "error", "message": str(e)}
