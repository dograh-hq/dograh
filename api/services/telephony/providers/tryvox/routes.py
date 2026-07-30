"""TryVox Answer and lifecycle webhook routes."""

import json

from fastapi import APIRouter, HTTPException, Request, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import JSONResponse

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


@router.websocket("/tryvox/ws/{workflow_id}/{organization_id}/{workflow_run_id}")
async def handle_tryvox_websocket(
    websocket: WebSocket,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
):
    """Accept TryVox's required media subprotocol, then use Dograh's handler."""
    from api.routes.telephony import _handle_telephony_websocket

    await websocket.accept(subprotocol="audio.drachtio.org")
    await _handle_telephony_websocket(
        websocket, workflow_id, organization_id, workflow_run_id
    )


async def _read_signed_json(request: Request) -> tuple[dict, str]:
    body_bytes = await request.body()
    try:
        raw_body = body_bytes.decode("utf-8")
        data = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400, detail="Webhook body must be JSON"
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be an object")
    return data, raw_body


async def _resolve_provider(workflow_run_id: int, organization_id: int | None = None):
    if organization_id is not None:
        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
    else:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if organization_id is not None and workflow.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Workflow not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    if provider.PROVIDER_NAME != "tryvox":
        raise HTTPException(status_code=400, detail="Workflow run provider mismatch")
    return workflow_run, workflow, provider


async def _verify_request(
    request: Request,
    provider,
    callback_data: dict,
    raw_body: str,
) -> None:
    valid = await provider.verify_inbound_signature(
        str(request.url), callback_data, dict(request.headers), raw_body
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _assert_call_matches(workflow_run, callback_data: dict) -> None:
    expected = (workflow_run.gathered_context or {}).get("call_id")
    received = callback_data.get("call_uuid") or callback_data.get("CallUUID")
    if expected and received and str(expected) != str(received):
        raise HTTPException(status_code=403, detail="Webhook call does not match run")


@router.post("/tryvox/answer", include_in_schema=False)
async def handle_tryvox_answer(
    request: Request,
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
):
    """Verify the signed Answer request and return VoxML Stream instructions."""
    set_current_run_id(workflow_run_id)
    callback_data, raw_body = await _read_signed_json(request)
    workflow_run, workflow, provider = await _resolve_provider(
        workflow_run_id, organization_id
    )
    if workflow.id != workflow_id:
        raise HTTPException(status_code=400, detail="Workflow ID mismatch")
    await _verify_request(request, provider, callback_data, raw_body)
    _assert_call_matches(workflow_run, callback_data)

    response_content = await provider.get_webhook_response(
        workflow_id, organization_id, workflow_run_id
    )
    return JSONResponse(json.loads(response_content))


@router.post("/tryvox/status/{workflow_run_id}")
async def handle_tryvox_status(
    request: Request,
    workflow_run_id: int,
):
    """Verify and process TryVox ringing, answered, and hangup callbacks."""
    set_current_run_id(workflow_run_id)
    callback_data, raw_body = await _read_signed_json(request)
    workflow_run, _, provider = await _resolve_provider(workflow_run_id)
    await _verify_request(request, provider, callback_data, raw_body)
    _assert_call_matches(workflow_run, callback_data)

    parsed = provider.parse_status_callback(callback_data)
    if not parsed["call_id"]:
        raise HTTPException(status_code=400, detail="Callback missing CallUUID")

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
    logger.info(
        f"[run {workflow_run_id}] Processed TryVox status "
        f"{parsed['status']} for call {parsed['call_id']}"
    )
    return {"status": "success"}
