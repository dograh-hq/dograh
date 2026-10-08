import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api.db.models import UserModel
from api.schemas.workflow_builder import (
    BuilderSaveRequest,
    BuilderSaveResponse,
    BuilderSession,
    BuilderTurnRequest,
)
from api.services.auth.depends import get_user
from api.services.workflow.builder import (
    builder_turn,
    encode_event,
    load_session,
    save_builder_draft,
)

router = APIRouter(prefix="/workflow/builder", tags=["Workflow Builder"])


@router.get("/{session_id}", response_model=BuilderSession)
async def get_builder_session(session_id: UUID, user: UserModel = Depends(get_user)):
    return await load_session(user, session_id)


@router.post(
    "/{session_id}/turn",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def send_builder_turn(
    session_id: UUID, request: BuilderTurnRequest, user: UserModel = Depends(get_user)
):
    if not user.selected_organization_id:
        raise HTTPException(403, "Select an organization first")

    async def events():
        # Send heartbeats during model calls to keep proxies from buffering/timing out.
        queue: asyncio.Queue = asyncio.Queue()

        async def pump():
            try:
                async for event in builder_turn(user, session_id, request):
                    await queue.put(event)
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    break
                yield encode_event(event)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/save", response_model=BuilderSaveResponse)
async def save_builder_session(
    session_id: UUID, request: BuilderSaveRequest, user: UserModel = Depends(get_user)
):
    return BuilderSaveResponse(
        workflow_id=await save_builder_draft(user, session_id, request.checkpoint)
    )
