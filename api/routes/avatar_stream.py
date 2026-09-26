"""Avatar relay WebSocket (host mode).

Streams the encoded audio + animation messages produced by the run's
SpatialReal ``AvatarRunSession`` (see ``api/services/avatar``) to the
browser, which feeds them to the AvatarKit web SDK in
``DrivingServiceMode.host`` via ``yieldAudioData`` / ``yieldFramesData``.

Binary frames carry a 2-byte header (type, flags) added by the session
manager; JSON text frames carry control messages (ready / error /
interrupted). The payloads are opaque server-SDK messages and are relayed
verbatim.
"""

import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from loguru import logger
from starlette.websockets import WebSocketState

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user_ws
from api.services.avatar import get_avatar_session

router = APIRouter(prefix="/ws/avatar", tags=["avatar"])

# How long the browser may connect before the pipeline has created the
# session (the WS usually connects while the call is still being set up).
SESSION_WAIT_SECONDS = 30
SESSION_POLL_INTERVAL = 0.25


@router.websocket("/{workflow_run_id}")
async def avatar_stream(
    websocket: WebSocket,
    workflow_run_id: int,
    user: UserModel = Depends(get_user_ws),
):
    await websocket.accept()

    # Organization scoping: the run must belong to the caller's org.
    workflow_run = await db_client.get_workflow_run(
        workflow_run_id, organization_id=user.selected_organization_id
    )
    if not workflow_run:
        await websocket.close(code=1008, reason="workflow run not found")
        return

    # Wait briefly for the pipeline to create the avatar session.
    session = None
    waited = 0.0
    while waited < SESSION_WAIT_SECONDS:
        session = get_avatar_session(workflow_run_id)
        if session is not None:
            break
        await asyncio.sleep(SESSION_POLL_INTERVAL)
        waited += SESSION_POLL_INTERVAL

    if session is None:
        await websocket.send_json(
            {"type": "avatar-error", "detail": "avatar session not available"}
        )
        await websocket.close(code=1000, reason="no avatar session")
        return

    logger.info(f"Avatar relay WS connected for run {workflow_run_id}")

    async def pump_relay():
        while True:
            item = await session.relay_queue.get()
            if websocket.application_state != WebSocketState.CONNECTED:
                return
            if isinstance(item, dict):
                await websocket.send_json(item)
            else:
                await websocket.send_bytes(item)

    async def drain_client():
        # The browser doesn't send data; this just surfaces disconnects.
        while True:
            await websocket.receive_text()

    pump_task = asyncio.create_task(pump_relay())
    drain_task = asyncio.create_task(drain_client())
    try:
        done, pending = await asyncio.wait(
            {pump_task, drain_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning(
                    f"Avatar relay WS error for run {workflow_run_id}: {exc}"
                )
    except WebSocketDisconnect:
        pass
    finally:
        for task in (pump_task, drain_task):
            task.cancel()
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
        logger.info(f"Avatar relay WS closed for run {workflow_run_id}")
