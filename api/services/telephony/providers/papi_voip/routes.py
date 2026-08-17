"""Papi Voip telephony routes (webhooks, status callbacks, answer URLs).

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


@router.api_route("/papi-voip-webhook", methods=["GET", "POST"], include_in_schema=False)
async def handle_papi_voip_webhook(
    request: Request,
    workflow_id: Optional[int] = None,
    workflow_run_id: Optional[int] = None,
    organization_id: Optional[int] = None,
):
    """Handle Papi Voip answering webhook and status callbacks."""
    # Resolve parameters from query string if not already provided
    qp = request.query_params
    workflow_id = workflow_id or (int(qp["workflow_id"]) if "workflow_id" in qp else None)
    workflow_run_id = workflow_run_id or (
        int(qp["workflow_run_id"]) if "workflow_run_id" in qp else None
    )
    organization_id = organization_id or (
        int(qp["organization_id"]) if "organization_id" in qp else None
    )

    if not workflow_run_id or not organization_id:
        return {"status": "ok"}

    set_current_run_id(workflow_run_id)

    body_data = {}
    if request.method == "POST":
        try:
            body_data = await request.json()
        except Exception:
            try:
                form = await request.form()
                body_data = dict(form)
            except Exception:
                body_data = {}

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run or (
        organization_id and workflow_run.organization_id != organization_id
    ):
        logger.error(
            f"[run {workflow_run_id}] Workflow run not found or organization mismatch for Papi Voip webhook"
        )
        return {"error": "workflow_run_not_found"}

    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    is_valid = await provider.verify_webhook_signature(
        str(request.url),
        body_data,
        signature=request.headers.get("x-papi-signature", ""),
    )
    if not is_valid:
        logger.warning(f"[run {workflow_run_id}] Invalid Papi Voip webhook signature")
        return {"status": "error", "reason": "invalid_signature"}

    # Process status callbacks
    if body_data and ("status" in body_data or "event" in body_data):
        parsed = provider.parse_status_callback(body_data)
        if parsed and parsed.get("status"):
            await _process_status_update(
                workflow_run_id,
                StatusCallbackRequest(
                    call_id=parsed.get("call_id") or str(workflow_run_id),
                    status=parsed.get("status"),
                    from_number=parsed.get("from_number"),
                    to_number=parsed.get("to_number"),
                    direction=parsed.get("direction"),
                    duration=(
                        str(parsed.get("duration"))
                        if parsed.get("duration") is not None
                        else None
                    ),
                    extra=body_data,
                ),
            )
            return {"status": "ok"}

    # Answering hook stream payload
    response_content = await provider.get_webhook_response(
        workflow_id or workflow_run.workflow_id, organization_id, workflow_run_id
    )
    return json.loads(response_content)
