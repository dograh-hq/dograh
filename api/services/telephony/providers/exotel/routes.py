"""Exotel telephony routes.

Mounted under /api/v1/telephony by api.routes.telephony via importlib.

Architecture (App Bazaar flow):
  1. User configures an Exotel App Bazaar flow with WebSocket URL:
       wss://{BACKEND}/api/v1/telephony/exotel/stream
  2. When initiating an outbound call, we pass the App Bazaar URL as `Url`.
  3. Exotel calls the number, answers, then connects to our fixed WebSocket.
  4. We identify the call by CallSid (stored on workflow_run at initiation via
     cost_info["call_id"] by run_pipeline_telephony, or gathered_context).
"""

import json

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
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

    # Exotel sends 'connected' first, then 'start' — same as Twilio.
    # Loop until we get the 'start' event (skip 'connected' and any other preamble).
    start_msg = None
    for _ in range(5):  # safety limit
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            logger.warning("[Exotel stream] WebSocket disconnected before start event")
            return

        logger.info(f"[Exotel stream] Received message raw: {raw}")

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"[Exotel stream] Non-JSON message: {raw}")
            await websocket.close(code=4400, reason="Expected JSON")
            return

        event_type = (msg.get("event") or msg.get("Event") or "").lower()

        if event_type == "connected":
            logger.info("[Exotel stream] Got 'connected' event, waiting for 'start'...")
            continue

        if event_type == "start":
            start_msg = msg
            break

        logger.warning(
            f"[Exotel stream] Unexpected event {event_type!r} before start, skipping."
        )

    if start_msg is None:
        logger.error("[Exotel stream] Never received 'start' event from Exotel")
        await websocket.close(code=4400, reason="Expected start event")
        return

    # Exotel may nest stream metadata under 'start' or at top level.
    # Keys are snake_case (stream_sid, call_sid) — Twilio-compatible format.
    start_data = start_msg.get("start") or start_msg
    call_sid = (
        start_data.get("call_sid")
        or start_data.get("callSid")
        or start_data.get("CallSid")
        or start_msg.get("call_sid")
        or start_msg.get("callSid")
    )
    stream_sid = (
        start_data.get("stream_sid")
        or start_data.get("streamSid")
        or start_data.get("streamId")
        or start_msg.get("stream_sid")
        or start_msg.get("streamSid")
        or ""
    )
    account_sid = (
        start_data.get("account_sid")
        or start_data.get("accountSid")
        or start_msg.get("account_sid")
        or ""
    )

    logger.info(
        f"[Exotel stream] callSid={call_sid!r} streamSid={stream_sid!r} accountSid={account_sid!r}"
    )

    if not call_sid:
        logger.error(
            f"[Exotel stream] Missing callSid in start event. Full msg: {start_msg}"
        )
        await websocket.close(code=4400, reason="Missing callSid")
        return

    # ------------------------------------------------------------------
    # 2. Look up the workflow_run by callSid
    # ------------------------------------------------------------------
    workflow_run = await db_client.get_workflow_run_by_call_id(call_sid)

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

    logger.info(
        f"[Exotel stream] Matched callSid={call_sid} → "
        f"workflow_run_id={workflow_run_id} workflow_id={workflow_id}"
    )

    # ------------------------------------------------------------------
    # 3. Run the pipeline
    # ------------------------------------------------------------------
    from api.services.pipecat.run_pipeline import run_pipeline_telephony

    await run_pipeline_telephony(
        websocket,
        provider_name="exotel",
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        user_id=user_id,
        call_id=call_sid,
        transport_kwargs={
            "stream_id": stream_sid,
            "call_id": call_sid,
            "account_sid": account_sid,
        },
    )


# ---------------------------------------------------------------------------
# Status callback — called by Exotel on call completion / state changes.
# ---------------------------------------------------------------------------


@router.post("/exotel/status-callback/{workflow_run_id}")
async def handle_exotel_status_callback(
    workflow_run_id: int,
    request: Request,
):
    """Handle Exotel StatusCallback POST."""
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
