"""TryVox Answer and lifecycle webhook routes."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import JSONResponse

from api.db import db_client
from api.enums import WorkflowRunState
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

from .security import tryvox_security

router = APIRouter()


@router.websocket("/tryvox/ws/{workflow_id}/{organization_id}/{workflow_run_id}")
async def handle_tryvox_websocket(
    websocket: WebSocket,
    workflow_id: int,
    organization_id: int,
    workflow_run_id: int,
):
    """Redeem a one-shot capability, then accept TryVox's media protocol."""
    from api.routes.telephony import _handle_telephony_websocket

    token = websocket.query_params.get("token", "")
    reservation = await tryvox_security.reserve_stream_token(
        workflow_id,
        organization_id,
        workflow_run_id,
        token,
    )
    if not reservation:
        await websocket.close(code=4401, reason="Invalid stream capability")
        return

    committed = False

    async def commit_stream() -> bool:
        nonlocal committed
        consumed = await tryvox_security.consume_stream_token(
            workflow_id,
            organization_id,
            workflow_run_id,
            reservation,
        )
        if not consumed:
            return False
        try:
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                state=WorkflowRunState.RUNNING.value,
            )
        except BaseException:
            try:
                await asyncio.shield(
                    tryvox_security.rollback_consumed_stream_token(
                        workflow_id,
                        organization_id,
                        workflow_run_id,
                        reservation,
                        token,
                    )
                )
            except Exception:
                logger.exception(
                    f"[run {workflow_run_id}] Failed to roll back consumed "
                    "TryVox stream capability"
                )
            raise
        committed = True
        return True

    try:
        await websocket.accept(subprotocol="audio.drachtio.org")
        await _handle_telephony_websocket(
            websocket,
            workflow_id,
            organization_id,
            workflow_run_id,
            provider_route_authenticated=True,
            on_provider_ready=commit_stream,
        )
    finally:
        if not committed:
            try:
                await asyncio.shield(
                    tryvox_security.release_stream_token(
                        workflow_id,
                        organization_id,
                        workflow_run_id,
                        reservation,
                        token,
                    )
                )
            except Exception:
                logger.exception(
                    f"[run {workflow_run_id}] Failed to release "
                    "TryVox stream capability"
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
) -> str:
    valid = await provider.verify_inbound_signature(
        str(request.url), callback_data, dict(request.headers), raw_body
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    return provider._signature_timestamp(request.headers.get("x-tryvox-signature", ""))


async def _release_callback_reservation(
    provider,
    callback_type: str,
    workflow_run_id: int,
    timestamp: str,
    raw_body: str,
    owner: str,
) -> None:
    try:
        await asyncio.shield(
            tryvox_security.release_callback(
                provider.auth_id,
                callback_type,
                workflow_run_id,
                timestamp,
                raw_body,
                owner,
            )
        )
    except Exception:
        logger.exception(
            f"[run {workflow_run_id}] Failed to release "
            f"TryVox {callback_type} callback reservation"
        )


def _assert_call_matches(workflow_run, callback_data: dict) -> None:
    expected = (workflow_run.gathered_context or {}).get("call_id")
    received = callback_data.get("call_uuid") or callback_data.get("CallUUID")
    if not expected or not received or str(expected) != str(received):
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
    timestamp = await _verify_request(request, provider, callback_data, raw_body)
    _assert_call_matches(workflow_run, callback_data)
    claim_state, owner = await tryvox_security.reserve_callback(
        provider.auth_id,
        "answer",
        workflow_run_id,
        timestamp,
        raw_body,
    )
    if claim_state == "in_progress":
        raise HTTPException(status_code=409, detail="Callback processing in progress")
    if claim_state == "completed":
        logger.info(
            f"[run {workflow_run_id}] Returning idempotent Answer response "
            "for duplicate TryVox callback"
        )
        response_content = await provider.get_webhook_response(
            workflow_id, organization_id, workflow_run_id
        )
        return JSONResponse(json.loads(response_content))

    assert owner is not None
    try:
        response_content = await provider.get_webhook_response(
            workflow_id, organization_id, workflow_run_id
        )
        finalized = await tryvox_security.finalize_callback(
            provider.auth_id,
            "answer",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
        )
        if not finalized:
            raise HTTPException(status_code=503, detail="Callback claim expired")
    except BaseException:
        await _release_callback_reservation(
            provider,
            "answer",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
        )
        raise
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
    timestamp = await _verify_request(request, provider, callback_data, raw_body)
    _assert_call_matches(workflow_run, callback_data)
    parsed = provider.parse_status_callback(callback_data)
    if not parsed["call_id"]:
        raise HTTPException(status_code=400, detail="Callback missing CallUUID")

    claim_state, owner = await tryvox_security.reserve_callback(
        provider.auth_id,
        "status",
        workflow_run_id,
        timestamp,
        raw_body,
    )
    if claim_state == "completed":
        return {"status": "success", "duplicate": True}
    if claim_state == "in_progress":
        raise HTTPException(status_code=409, detail="Callback processing in progress")

    assert owner is not None
    try:
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
        finalized = await tryvox_security.finalize_callback(
            provider.auth_id,
            "status",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
        )
        if not finalized:
            raise HTTPException(status_code=503, detail="Callback claim expired")
    except BaseException:
        await _release_callback_reservation(
            provider,
            "status",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
        )
        raise
    logger.info(
        f"[run {workflow_run_id}] Processed TryVox status "
        f"{parsed['status']} for call {parsed['call_id']}"
    )
    return {"status": "success"}
