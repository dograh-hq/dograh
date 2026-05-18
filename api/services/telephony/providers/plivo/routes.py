"""Plivo telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from typing import Optional

from fastapi import APIRouter, Header, Request
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


async def _handle_plivo_status_callback(
    workflow_run_id: int,
    request: Request,
    x_plivo_signature_v3: Optional[str],
    x_plivo_signature_ma_v3: Optional[str],
    x_plivo_signature_v3_nonce: Optional[str],
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

    signature = x_plivo_signature_v3 or x_plivo_signature_ma_v3
    if signature:
        backend_endpoint, _ = await get_backend_endpoints()
        callback_kind = request.url.path.split("/")[-2]
        full_url = f"{backend_endpoint}/api/v1/telephony/plivo/{callback_kind}/{workflow_run_id}"
        is_valid = await provider.verify_inbound_signature(
            full_url,
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
    user_id: int,
    workflow_run_id: int,
    organization_id: int,
    request: Request,
    x_plivo_signature_v3: Optional[str] = Header(None),
    x_plivo_signature_ma_v3: Optional[str] = Header(None),
    x_plivo_signature_v3_nonce: Optional[str] = Header(None),
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

    signature = x_plivo_signature_v3 or x_plivo_signature_ma_v3
    if signature:
        backend_endpoint, _ = await get_backend_endpoints()
        full_url = (
            f"{backend_endpoint}/api/v1/telephony/plivo-xml"
            f"?workflow_id={workflow_id}"
            f"&user_id={user_id}"
            f"&workflow_run_id={workflow_run_id}"
            f"&organization_id={organization_id}"
        )
        is_valid = await provider.verify_inbound_signature(
            full_url, callback_data, dict(request.headers)
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
        workflow_id, user_id, workflow_run_id
    )
    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/plivo/hangup-callback/{workflow_run_id}")
async def handle_plivo_hangup_callback(
    workflow_run_id: int,
    request: Request,
    x_plivo_signature_v3: Optional[str] = Header(None),
    x_plivo_signature_ma_v3: Optional[str] = Header(None),
    x_plivo_signature_v3_nonce: Optional[str] = Header(None),
):
    """Handle Plivo hangup callbacks."""
    return await _handle_plivo_status_callback(
        workflow_run_id,
        request,
        x_plivo_signature_v3,
        x_plivo_signature_ma_v3,
        x_plivo_signature_v3_nonce,
    )


@router.post("/plivo/ring-callback/{workflow_run_id}")
async def handle_plivo_ring_callback(
    workflow_run_id: int,
    request: Request,
    x_plivo_signature_v3: Optional[str] = Header(None),
    x_plivo_signature_ma_v3: Optional[str] = Header(None),
    x_plivo_signature_v3_nonce: Optional[str] = Header(None),
):
    """Handle Plivo ring callbacks."""
    return await _handle_plivo_status_callback(
        workflow_run_id,
        request,
        x_plivo_signature_v3,
        x_plivo_signature_ma_v3,
        x_plivo_signature_v3_nonce,
    )
