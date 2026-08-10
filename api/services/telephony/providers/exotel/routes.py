"""Exotel telephony routes.

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry.
"""

import hmac
import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


async def _parse_callback_body(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        return dict(await request.form())
    except Exception as e:
        logger.warning(f"Exotel status callback body parse failed: {e}")
        return {}


@router.post("/exotel/status-callback/{workflow_run_id}")
async def handle_exotel_status_callback(workflow_run_id: int, request: Request):
    set_current_run_id(workflow_run_id)

    callback_data = await _parse_callback_body(request)
    if not callback_data:
        return {"status": "ignored", "reason": "malformed_body"}

    logger.info(
        f"[run {workflow_run_id}] Exotel status callback: {json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
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
        logger.warning(
            f"[run {workflow_run_id}] Invalid Exotel status callback auth"
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    parsed = provider.parse_status_callback(callback_data)
    expected_call_id = None
    gathered = workflow_run.gathered_context or {}
    if isinstance(gathered, dict):
        expected_call_id = gathered.get("call_id")
    if expected_call_id:
        presented = parsed.get("call_id") or ""
        if not hmac.compare_digest(str(expected_call_id), str(presented)):
            logger.warning(
                f"[run {workflow_run_id}] Exotel status CallSid mismatch "
                f"expected={expected_call_id!r} got={presented!r}"
            )
            raise HTTPException(status_code=403, detail="CallSid mismatch")

    await _process_status_update(
        workflow_run_id,
        StatusCallbackRequest(
            call_id=parsed["call_id"],
            status=parsed["status"],
            from_number=parsed.get("from_number"),
            to_number=parsed.get("to_number"),
            direction=parsed.get("direction"),
            duration=parsed.get("duration"),
            extra=parsed.get("extra", {}),
        ),
    )
    return {"status": "success"}
