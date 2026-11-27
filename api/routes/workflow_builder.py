from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.db.models import UserModel
from api.schemas.workflow_builder import (
    BuilderCancelResponse,
    BuilderSaveRequest,
    BuilderSaveResponse,
    BuilderSession,
    BuilderTurnRequest,
)
from api.services.auth.depends import get_user
from api.services.workflow.builder import (
    open_workflow_builder,
    save_builder_draft,
)
from api.services.workflow.builder_runtime.execution import (
    admit_builder_turn,
    cancel_run,
    get_run,
    session_with_execution,
    stream_builder_run,
)

BuilderUser = Annotated[UserModel, Depends(get_user)]

router = APIRouter(prefix="/workflow/builder", tags=["Workflow Builder"])


@router.post("/workflow/{workflow_id}/session", response_model=BuilderSession)
async def open_workflow_builder_session(workflow_id: int, user: BuilderUser):
    session = await open_workflow_builder(user, workflow_id)
    return await session_with_execution(user, session.id, session=session)


@router.get("/{session_id}", response_model=BuilderSession)
async def get_builder_session(session_id: UUID, user: BuilderUser):
    return await session_with_execution(user, session_id)


@router.post(
    "/{session_id}/turn",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def send_builder_turn(
    session_id: UUID, request: BuilderTurnRequest, user: BuilderUser
):
    if not user.selected_organization_id:
        raise HTTPException(403, "Select an organization first")

    run = await admit_builder_turn(user, session_id, request)
    return StreamingResponse(
        stream_builder_run(user, session_id, run["id"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{session_id}/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def get_builder_run_events(
    session_id: UUID,
    run_id: UUID,
    user: BuilderUser,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None),
):
    await get_run(user, session_id, run_id)
    if last_event_id is not None:
        try:
            after = max(after, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(422, "Invalid event cursor") from exc
    return StreamingResponse(
        stream_builder_run(user, session_id, str(run_id), after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/runs/{run_id}/cancel", response_model=BuilderCancelResponse)
async def cancel_builder_run(session_id: UUID, run_id: UUID, user: BuilderUser):
    return await cancel_run(user, session_id, run_id)


@router.post("/{session_id}/save", response_model=BuilderSaveResponse)
async def save_builder_session(
    session_id: UUID, request: BuilderSaveRequest, user: BuilderUser
):
    return BuilderSaveResponse(
        workflow_id=await save_builder_draft(
            user, session_id, request.checkpoint, request.workflow_id
        )
    )
