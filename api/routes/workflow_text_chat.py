from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pipecat.utils.run_context import set_current_run_id
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel, WorkflowRunTextSessionModel
from api.enums import WorkflowRunMode
from api.services.auth.depends import get_user_with_selected_organization
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
    TextChatTurnNotFoundError,
    append_text_chat_user_message,
    default_text_chat_checkpoint,
    default_text_chat_session_data,
    execute_pending_text_chat_turn,
    initialize_text_chat_session,
    normalize_text_chat_checkpoint,
    normalize_text_chat_session_data,
    rewind_text_chat_session_state,
)

router = APIRouter(prefix="/workflow", tags=["workflow-text-chat"])


class CreateTextChatSessionRequest(BaseModel):
    name: str | None = None
    initial_context: Dict[str, Any] | None = None
    annotations: Dict[str, Any] | None = None


class AppendTextChatMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    expected_revision: int | None = None


class RewindTextChatSessionRequest(BaseModel):
    cursor_turn_id: str | None = None
    expected_revision: int | None = None


class WorkflowRunTextSessionResponse(BaseModel):
    workflow_run_id: int
    workflow_id: int
    name: str
    mode: str
    state: str
    is_completed: bool
    revision: int
    initial_context: Dict[str, Any] | None = None
    gathered_context: Dict[str, Any] | None = None
    annotations: Dict[str, Any] | None = None
    session_data: Dict[str, Any]
    checkpoint: Dict[str, Any]
    created_at: datetime
    updated_at: datetime | None = None


def _get_state_value(state: Any) -> str:
    return state.value if hasattr(state, "value") else str(state)


def _build_response(
    text_session: WorkflowRunTextSessionModel,
) -> WorkflowRunTextSessionResponse:
    workflow_run = text_session.workflow_run
    return WorkflowRunTextSessionResponse(
        workflow_run_id=workflow_run.id,
        workflow_id=workflow_run.workflow_id,
        name=workflow_run.name,
        mode=workflow_run.mode,
        state=_get_state_value(workflow_run.state),
        is_completed=workflow_run.is_completed,
        revision=text_session.revision,
        initial_context=workflow_run.initial_context,
        gathered_context=workflow_run.gathered_context,
        annotations=workflow_run.annotations,
        session_data=normalize_text_chat_session_data(text_session.session_data),
        checkpoint=normalize_text_chat_checkpoint(text_session.checkpoint),
        created_at=text_session.created_at,
        updated_at=text_session.updated_at,
    )


def _revision_conflict_detail(e: Any) -> dict[str, Any]:
    return {
        "message": "Text chat session revision conflict",
        "expected_revision": e.expected_revision,
        "actual_revision": e.actual_revision,
    }


async def _ensure_text_chat_quota(
    user: UserModel,
    workflow_id: int,
    workflow_run_id: int,
) -> None:
    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow_id,
        organization_id=user.selected_organization_id,
        workflow_run_id=workflow_run_id,
        actor_user=user,
    )
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)


async def _load_text_session_or_404(
    workflow_id: int,
    run_id: int,
    user: UserModel,
) -> WorkflowRunTextSessionModel:
    set_current_run_id(run_id)
    text_session = await db_client.get_workflow_run_text_session(
        run_id, organization_id=user.selected_organization_id
    )
    if not text_session or not text_session.workflow_run:
        raise HTTPException(status_code=404, detail="Text chat session not found")
    if text_session.workflow_run.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Text chat session not found")
    if text_session.workflow_run.mode != WorkflowRunMode.TEXTCHAT.value:
        raise HTTPException(
            status_code=400, detail="Workflow run is not a text chat session"
        )
    return text_session


async def _execute_pending_turn_response(
    *,
    workflow_id: int,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
) -> WorkflowRunTextSessionResponse:
    try:
        updated_text_session = await execute_pending_text_chat_turn(
            workflow_id=workflow_id,
            run_id=run_id,
            text_session=text_session,
        )
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))
    except TextChatPendingTurnLostError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except TextChatSessionExecutionError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return _build_response(updated_text_session)


@router.post(
    "/{workflow_id}/text-chat/sessions",
    response_model=WorkflowRunTextSessionResponse,
)
async def create_text_chat_session(
    workflow_id: int,
    request: CreateTextChatSessionRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> WorkflowRunTextSessionResponse:
    session_name = request.name or f"WR-TEXT-{uuid4().hex[:6].upper()}"
    try:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=user.selected_organization_id
        )
        if not workflow:
            raise ValueError(f"Workflow with ID {workflow_id} not found")
        run_inputs = await prepare_workflow_run_inputs(
            db_client,
            workflow,
            initial_context=request.initial_context,
            use_draft=True,
            include_template_context=True,
        )
        workflow_run = await db_client.create_workflow_run(
            name=session_name,
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=user.id,
            initial_context=run_inputs.initial_context,
            organization_id=user.selected_organization_id,
            definition_id=run_inputs.definition_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    set_current_run_id(workflow_run.id)
    await _ensure_text_chat_quota(user, workflow_id, workflow_run.id)

    annotations = {
        "tester": {
            "source": "workflow_editor",
            "modality": "text",
        }
    }
    if request.annotations:
        annotations = {**annotations, **request.annotations}
    workflow_run = await db_client.update_workflow_run(
        workflow_run.id,
        annotations=annotations,
    )

    text_session = await db_client.ensure_workflow_run_text_session(
        workflow_run.id,
        session_data=default_text_chat_session_data(),
        checkpoint=default_text_chat_checkpoint(),
    )

    try:
        text_session = await initialize_text_chat_session(
            run_id=workflow_run.id,
            text_session=text_session,
        )
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    return await _execute_pending_turn_response(
        workflow_id=workflow_id,
        run_id=workflow_run.id,
        text_session=text_session,
    )


@router.get(
    "/{workflow_id}/text-chat/sessions/{run_id}",
    response_model=WorkflowRunTextSessionResponse,
)
async def get_text_chat_session(
    workflow_id: int,
    run_id: int,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> WorkflowRunTextSessionResponse:
    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    return _build_response(text_session)


@router.post(
    "/{workflow_id}/text-chat/sessions/{run_id}/messages",
    response_model=WorkflowRunTextSessionResponse,
)
async def append_text_chat_message(
    workflow_id: int,
    run_id: int,
    request: AppendTextChatMessageRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> WorkflowRunTextSessionResponse:
    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    await _ensure_text_chat_quota(user, workflow_id, run_id)

    try:
        text_session = await append_text_chat_user_message(
            run_id=run_id,
            text_session=text_session,
            user_text=request.text,
            expected_revision=request.expected_revision,
        )
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    return await _execute_pending_turn_response(
        workflow_id=workflow_id,
        run_id=run_id,
        text_session=text_session,
    )


@router.post(
    "/{workflow_id}/text-chat/sessions/{run_id}/rewind",
    response_model=WorkflowRunTextSessionResponse,
)
async def rewind_text_chat_session(
    workflow_id: int,
    run_id: int,
    request: RewindTextChatSessionRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
) -> WorkflowRunTextSessionResponse:
    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    try:
        text_session = await rewind_text_chat_session_state(
            run_id=run_id,
            text_session=text_session,
            cursor_turn_id=request.cursor_turn_id,
            expected_revision=request.expected_revision,
        )
    except TextChatTurnNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TextChatSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    return _build_response(text_session)
