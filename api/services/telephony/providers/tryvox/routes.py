"""TryVox Answer and lifecycle webhook routes."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from starlette.responses import JSONResponse

from api.db import db_client
from api.enums import TelephonyCallStatus, WorkflowRunState
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

    async def rollback_stream() -> None:
        nonlocal committed
        if not committed:
            return

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            state=WorkflowRunState.INITIALIZED.value,
        )
        try:
            restored = await tryvox_security.rollback_consumed_stream_token(
                workflow_id,
                organization_id,
                workflow_run_id,
                reservation,
                token,
            )
            if not restored:
                raise RuntimeError("TryVox stream capability rollback was rejected")
        except BaseException:
            try:
                await asyncio.shield(
                    db_client.update_workflow_run(
                        run_id=workflow_run_id,
                        state=WorkflowRunState.RUNNING.value,
                    )
                )
            except Exception:
                logger.exception(
                    f"[run {workflow_run_id}] Failed to restore running state "
                    "after TryVox stream rollback failure"
                )
            raise
        committed = False

    try:
        await websocket.accept(subprotocol="audio.drachtio.org")
        await _handle_telephony_websocket(
            websocket,
            workflow_id,
            organization_id,
            workflow_run_id,
            provider_route_authenticated=True,
            on_provider_ready=commit_stream,
            on_provider_failure=rollback_stream,
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


async def _assert_call_matches(
    workflow_run, workflow_run_id: int, callback_data: dict
) -> None:
    """Verify the callback's call ID against the run, claiming it on first contact.

    Outbound call initiation persists ``call_id`` on the run only after the
    provider's REST call returns, but TryVox can deliver its signed Answer
    (or an early status) callback before that write lands. Rejecting those
    callbacks outright drops a genuine call. Since the caller already
    verified the request signature, the first signed callback for a run is
    trusted to claim the call ID; every later callback must match it exactly.
    """
    received = callback_data.get("call_uuid") or callback_data.get("CallUUID")
    if not received:
        raise HTTPException(status_code=403, detail="Webhook call does not match run")
    received = str(received)

    expected = (workflow_run.gathered_context or {}).get("call_id")
    if expected:
        if str(expected) != received:
            raise HTTPException(
                status_code=403, detail="Webhook call does not match run"
            )
        return

    bound = await tryvox_security.claim_call_id(workflow_run_id, received)
    if bound != received:
        raise HTTPException(status_code=403, detail="Webhook call does not match run")

    gathered_context = {**(workflow_run.gathered_context or {}), "call_id": received}
    await db_client.update_workflow_run(
        run_id=workflow_run_id, gathered_context=gathered_context
    )
    workflow_run.gathered_context = gathered_context


async def _assert_call_correlation(request: Request, workflow_run_id: int) -> None:
    supplied = request.query_params.get("correlation_token", "")
    if not await tryvox_security.verify_call_correlation(workflow_run_id, supplied):
        raise HTTPException(status_code=403, detail="Invalid callback correlation")


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
    await _assert_call_correlation(request, workflow_run_id)
    await _assert_call_matches(workflow_run, workflow_run_id, callback_data)
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

    finalized = await tryvox_security.finalize_callback(
        provider.auth_id,
        "answer",
        workflow_run_id,
        timestamp,
        raw_body,
        owner,
    )
    if not finalized:
        # Record completion only if the claim expired without being acquired
        # by a retry. Never overwrite a newer worker's active reservation.
        logger.warning(
            f"[run {workflow_run_id}] Lost TryVox answer callback claim "
            "after generating the response; attempting safe completion"
        )
        await tryvox_security.complete_callback_if_unclaimed(
            provider.auth_id,
            "answer",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
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
    timestamp = await _verify_request(request, provider, callback_data, raw_body)
    await _assert_call_correlation(request, workflow_run_id)
    await _assert_call_matches(workflow_run, workflow_run_id, callback_data)
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

    finalized = await tryvox_security.finalize_callback(
        provider.auth_id,
        "status",
        workflow_run_id,
        timestamp,
        raw_body,
        owner,
    )
    if not finalized:
        # Record completion only if the claim expired without being acquired
        # by a retry. Never overwrite a newer worker's active reservation.
        logger.warning(
            f"[run {workflow_run_id}] Lost TryVox status callback claim "
            "after processing; attempting safe completion"
        )
        await tryvox_security.complete_callback_if_unclaimed(
            provider.auth_id,
            "status",
            workflow_run_id,
            timestamp,
            raw_body,
            owner,
        )
    if TelephonyCallStatus.from_raw(parsed["status"]) in {
        TelephonyCallStatus.COMPLETED,
        TelephonyCallStatus.FAILED,
        TelephonyCallStatus.BUSY,
        TelephonyCallStatus.NO_ANSWER,
        TelephonyCallStatus.CANCELED,
        TelephonyCallStatus.ERROR,
    }:
        await tryvox_security.retire_call_correlation(
            workflow_run_id,
            request.query_params["correlation_token"],
        )
    logger.info(
        f"[run {workflow_run_id}] Processed TryVox status "
        f"{parsed['status']} for call {parsed['call_id']}"
    )
    return {"status": "success"}
