"""Exotel telephony routes.

Mounted under /api/v1/telephony by api.routes.telephony via importlib.

Architecture (App Bazaar flow):
  1. User configures an Exotel App Bazaar flow with WebSocket URL:
       wss://{BACKEND}/api/v1/telephony/exotel/stream
  2. When initiating an outbound call, we pass the App Bazaar URL as `Url`.
  3. Exotel calls the number, answers, then connects to our fixed WebSocket.
  4. We identify the call by CallSid (stored on workflow_run at initiation).
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.utils.run_context import set_current_run_id
from sqlalchemy import select, text

from api.db import db_client
from api.db.models import WorkflowRun
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Fixed WebSocket stream endpoint — URL configured in Exotel App Bazaar.
# Exotel connects here when a call is answered (outbound or inbound).
# ---------------------------------------------------------------------------


@router.websocket("/exotel/stream")
async def exotel_stream(websocket: WebSocket):
    """
    Fixed WebSocket endpoint pre-configured in the Exotel App Bazaar flow.

    Exotel sends a 'start' event with CallSid. We look up the workflow_run
    that has that CallSid (stored at call-initiation time) and run the
    pipecat pipeline for it.
    """
    await websocket.accept()

    # ------------------------------------------------------------------
    # 1. Read the start event
    # ------------------------------------------------------------------
    try:
        raw = await websocket.receive_text()
    except WebSocketDisconnect:
        logger.warning("[Exotel stream] WebSocket disconnected before start event")
        return

    logger.info(f"[Exotel stream] Start message raw: {raw}")

    try:
        start_msg = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"[Exotel stream] Non-JSON start message: {raw}")
        await websocket.close(code=4400, reason="Expected JSON start event")
        return

    event_type = (start_msg.get("event") or start_msg.get("Event") or "").lower()
    if event_type != "start":
        logger.error(
            f"[Exotel stream] Expected 'start' event, got: {event_type!r}. "
            f"Full message: {start_msg}"
        )
        await websocket.close(code=4400, reason="Expected start event")
        return

    # Exotel may nest stream metadata under 'start' or at top level.
    start_data = start_msg.get("start") or start_msg
    call_sid = (
        start_data.get("callSid")
        or start_data.get("CallSid")
        or start_data.get("call_sid")
        or start_msg.get("callSid")
        or start_msg.get("CallSid")
    )
    stream_id = (
        start_data.get("streamId")
        or start_data.get("StreamId")
        or start_msg.get("streamId")
        or ""
    )

    logger.info(
        f"[Exotel stream] callSid={call_sid!r} streamId={stream_id!r}"
    )

    if not call_sid:
        logger.error(
            f"[Exotel stream] Missing callSid in start event. Full msg: {start_msg}"
        )
        await websocket.close(code=4400, reason="Missing callSid")
        return

    # ------------------------------------------------------------------
    # 2. Look up the workflow_run by callSid stored in gathered_context
    # ------------------------------------------------------------------
    workflow_run = await _find_workflow_run_by_call_sid(call_sid)

    if not workflow_run:
        logger.error(
            f"[Exotel stream] No workflow_run found for callSid={call_sid}. "
            "Ensure the call was initiated via Dograh before Exotel connects."
        )
        await websocket.close(code=4404, reason="Workflow run not found for call")
        return

    workflow_run_id = workflow_run.id
    workflow_id = workflow_run.workflow_id
    set_current_run_id(workflow_run_id)

    # Resolve user_id from the workflow
    workflow = await db_client.get_workflow_by_id(workflow_id)
    if not workflow:
        logger.error(f"[Exotel stream] Workflow {workflow_id} not found")
        await websocket.close(code=4404, reason="Workflow not found")
        return

    user_id = workflow.user_id
    organization_id = workflow.organization_id

    logger.info(
        f"[Exotel stream] Matched callSid={call_sid} → "
        f"workflow_run_id={workflow_run_id} workflow_id={workflow_id}"
    )

    # ------------------------------------------------------------------
    # 3. Run the pipeline — same as other telephony providers
    # ------------------------------------------------------------------
    from api.services.pipecat.run_pipeline import run_pipeline_telephony

    await run_pipeline_telephony(
        websocket,
        provider_name="exotel",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        user_id=user_id,
        call_id=call_sid,
        transport_kwargs={"stream_id": stream_id, "call_id": call_sid},
    )


async def _find_workflow_run_by_call_sid(call_sid: str):
    """
    Find the most recent workflow_run whose gathered_context contains
    {'call_id': call_sid}.  Uses a JSONB containment query (PostgreSQL).
    """
    try:
        async with db_client.get_session() as session:
            result = await session.execute(
                select(WorkflowRun)
                .where(
                    WorkflowRun.gathered_context.op("@>")(
                        text(f"'{{\"call_id\": \"{call_sid}\"}}'::jsonb")
                    )
                )
                .order_by(WorkflowRun.created_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.error(f"[Exotel stream] DB lookup failed for callSid={call_sid}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Status callback — called by Exotel on call completion / state changes.
# ---------------------------------------------------------------------------


@router.post("/exotel/status-callback/{workflow_run_id}")
async def handle_exotel_status_callback(
    workflow_run_id: int,
    request,
):
    """Handle Exotel StatusCallback POST."""
    from fastapi import Request

    request: Request
    set_current_run_id(workflow_run_id)

    form_data = await request.form()
    callback_data = dict(form_data)
    logger.info(
        f"[run {workflow_run_id}] Exotel status callback: "
        f"{json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Exotel status callback: workflow run not found"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(
            f"[run {workflow_run_id}] Exotel status callback: workflow not found"
        )
        return {"status": "ignored", "reason": "workflow_not_found"}

    from api.services.telephony.factory import get_telephony_provider_for_run

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

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
