import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import CallType, WorkflowRunMode
from api.services.auth.depends import get_user
from api.services.sakinah.session_store import (
    create_pending_session,
    finish_session,
)
from api.services.sakinah.workflow import ensure_sakinah_workflow
from api.services.workflow.run_creation import prepare_workflow_run_inputs

router = APIRouter(prefix="/sakinah", tags=["sakinah"])


class CreateSessionRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=20_000)
    name: str | None = Field(default=None, max_length=200)


class CreateSessionResponse(BaseModel):
    session_id: uuid.UUID
    workflow_id: int
    workflow_run_id: int
    started_at: datetime


class TranscriptTurn(BaseModel):
    role: Literal["user", "sakinah"]
    text: str = Field(min_length=1)
    final: bool
    timestamp: datetime


class EndSessionRequest(BaseModel):
    turns: list[TranscriptTurn]
    ended_at: datetime | None = None
    timings: dict[str, float] | None = None


class EndSessionResponse(BaseModel):
    session_id: uuid.UUID
    saved: bool


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest, user: UserModel = Depends(get_user)
) -> CreateSessionResponse:
    scenario = request.scenario.strip()
    if not scenario:
        raise HTTPException(status_code=422, detail="Scenario cannot be blank")

    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)
    workflow = await ensure_sakinah_workflow(db_client, user)
    initial_context = {
        "scenario": scenario,
        "session_id": str(session_id),
        "direction": CallType.INBOUND.value,
    }
    run_inputs = await prepare_workflow_run_inputs(
        db_client, workflow, initial_context=initial_context
    )
    run = await db_client.create_workflow_run(
        request.name or f"Sakinah {session_id}",
        workflow.id,
        WorkflowRunMode.SMALLWEBRTC.value,
        user.id,
        call_type=CallType.INBOUND,
        organization_id=user.selected_organization_id,
        definition_id=run_inputs.definition_id,
        initial_context=run_inputs.initial_context,
    )
    create_pending_session(
        session_id=str(session_id),
        scenario=scenario,
        name=request.name,
        workflow_id=workflow.id,
        workflow_run_id=run.id,
        organization_id=user.selected_organization_id,
        started_at=started_at,
    )
    return CreateSessionResponse(
        session_id=session_id,
        workflow_id=workflow.id,
        workflow_run_id=run.id,
        started_at=started_at,
    )


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(
    session_id: uuid.UUID,
    request: EndSessionRequest,
    user: UserModel = Depends(get_user),
) -> EndSessionResponse:
    try:
        finish_session(
            session_id=str(session_id),
            organization_id=user.selected_organization_id,
            ended_at=request.ended_at or datetime.now(UTC),
            turns=[turn.model_dump(mode="json") for turn in request.turns],
            timings=request.timings,
        )
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status_code=404, detail="Session not found") from None
    return EndSessionResponse(session_id=session_id, saved=True)
