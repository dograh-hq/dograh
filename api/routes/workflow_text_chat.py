from datetime import UTC, datetime
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel, WorkflowRunTextSessionModel
from api.db.workflow_run_text_session_client import (
    WorkflowRunTextSessionRevisionConflictError,
)
from api.enums import WorkflowRunMode
from api.services.auth.depends import get_user
from api.services.workflow.text_chat_runner import (
    execute_text_chat_pending_turn,
    merge_text_chat_usage_info,
)

router = APIRouter(prefix="/workflow", tags=["workflow-text-chat"])

TEXT_CHAT_SESSION_VERSION = 1
TEXT_CHAT_CHECKPOINT_VERSION = 1


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


def _default_session_data() -> Dict[str, Any]:
    return {
        "version": TEXT_CHAT_SESSION_VERSION,
        "status": "idle",
        "cursor_turn_id": None,
        "turns": [],
        "discarded_future": [],
        "simulator": {
            "enabled": False,
            "config": {},
        },
    }


def _default_checkpoint() -> Dict[str, Any]:
    return {
        "version": TEXT_CHAT_CHECKPOINT_VERSION,
        "anchor_turn_id": None,
        "current_node_id": None,
        "messages": [],
        "gathered_context": {},
        "tool_state": {},
    }


def _normalize_session_data(session_data: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized = {
        **_default_session_data(),
        **(session_data or {}),
    }
    normalized["turns"] = list(normalized.get("turns") or [])
    normalized["discarded_future"] = list(normalized.get("discarded_future") or [])
    simulator = normalized.get("simulator") or {}
    normalized["simulator"] = {
        "enabled": bool(simulator.get("enabled", False)),
        "config": dict(simulator.get("config") or {}),
    }
    return normalized


def _normalize_checkpoint(checkpoint: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized = {
        **_default_checkpoint(),
        **(checkpoint or {}),
    }
    normalized["messages"] = list(normalized.get("messages") or [])
    normalized["gathered_context"] = dict(normalized.get("gathered_context") or {})
    normalized["tool_state"] = dict(normalized.get("tool_state") or {})
    return normalized


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
        session_data=_normalize_session_data(text_session.session_data),
        checkpoint=_normalize_checkpoint(text_session.checkpoint),
        created_at=text_session.created_at,
        updated_at=text_session.updated_at,
    )


def _validate_turn_cursor(
    session_data: Dict[str, Any], cursor_turn_id: str | None
) -> None:
    if cursor_turn_id is None:
        return
    if not any(turn.get("id") == cursor_turn_id for turn in session_data["turns"]):
        raise HTTPException(
            status_code=404, detail="Turn not found in text chat session"
        )


def _truncate_future_turns(
    session_data: Dict[str, Any],
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    cursor_turn_id = session_data.get("cursor_turn_id")
    turns = list(session_data.get("turns") or [])
    discarded_future = list(session_data.get("discarded_future") or [])

    if cursor_turn_id is None:
        return turns, discarded_future

    for index, turn in enumerate(turns):
        if turn.get("id") == cursor_turn_id:
            active_turns = turns[: index + 1]
            future_turns = turns[index + 1 :]
            if future_turns:
                discarded_future.append(
                    {
                        "rewound_from_turn_id": cursor_turn_id,
                        "discarded_at": datetime.now(UTC).isoformat(),
                        "turns": future_turns,
                    }
                )
            return active_turns, discarded_future

    raise HTTPException(status_code=404, detail="Turn not found in text chat session")


def _latest_completed_turn_id(turns: list[Dict[str, Any]]) -> str | None:
    for turn in reversed(turns):
        if turn.get("status") == "completed":
            return turn.get("id")
    return None


def _build_pending_turn(*, user_text: str | None) -> Dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": f"turn_{uuid4().hex[:12]}",
        "status": "pending",
        "created_at": now,
        "user_message": (
            {
                "text": user_text,
                "created_at": now,
            }
            if user_text is not None
            else None
        ),
        "assistant_message": None,
        "events": [],
        "usage": {},
    }


def _revision_conflict_detail(
    e: WorkflowRunTextSessionRevisionConflictError,
) -> dict[str, Any]:
    return {
        "message": "Text chat session revision conflict",
        "expected_revision": e.expected_revision,
        "actual_revision": e.actual_revision,
    }


async def _load_text_session_or_404(
    workflow_id: int,
    run_id: int,
    user: UserModel,
) -> WorkflowRunTextSessionModel:
    if user.selected_organization_id is None:
        raise HTTPException(
            status_code=403, detail="Organization context is required"
        )
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


async def _execute_pending_turn_and_build_response(
    *,
    workflow_id: int,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
    user: UserModel,
) -> WorkflowRunTextSessionResponse:
    try:
        execution = await execute_text_chat_pending_turn(
            workflow_run_id=run_id,
            workflow_id=workflow_id,
            session_data=_normalize_session_data(text_session.session_data),
            checkpoint=_normalize_checkpoint(text_session.checkpoint),
        )
    except Exception as e:
        failed_session_data = _normalize_session_data(text_session.session_data)
        failed_turns = list(failed_session_data.get("turns") or [])
        if failed_turns and failed_turns[-1].get("status") == "pending":
            failed_turns[-1]["status"] = "failed"
            failed_turns[-1]["events"] = [
                *(failed_turns[-1].get("events") or []),
                {
                    "type": "execution_error",
                    "created_at": datetime.now(UTC).isoformat(),
                    "payload": {"message": str(e)},
                },
            ]
            failed_session_data["turns"] = failed_turns
            failed_session_data["status"] = "error"
            try:
                await db_client.update_workflow_run_text_session(
                    run_id,
                    session_data=failed_session_data,
                    expected_revision=text_session.revision,
                )
            except WorkflowRunTextSessionRevisionConflictError:
                pass
        raise HTTPException(
            status_code=500, detail="Failed to execute text chat assistant turn"
        )

    completed_session_data = _normalize_session_data(text_session.session_data)
    completed_turns = list(completed_session_data.get("turns") or [])
    if not completed_turns or completed_turns[-1].get("status") != "pending":
        raise HTTPException(
            status_code=500,
            detail="Text chat session lost its pending turn before completion",
        )

    completed_turns[-1]["status"] = "completed"
    completed_turns[-1]["assistant_message"] = (
        {
            "text": execution.assistant_text,
            "created_at": execution.assistant_created_at,
        }
        if execution.assistant_text
        else None
    )
    completed_turns[-1]["events"] = execution.events
    completed_turns[-1]["usage"] = execution.usage
    completed_turns[-1]["checkpoint_after_turn"] = execution.checkpoint
    completed_session_data["turns"] = completed_turns
    completed_session_data["status"] = "idle"

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=completed_session_data,
            checkpoint=execution.checkpoint,
            expected_revision=text_session.revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    existing_usage_info = text_session.workflow_run.usage_info or {}
    merged_usage_info = merge_text_chat_usage_info(
        existing_usage_info,
        execution.usage,
    )
    await db_client.update_workflow_run(
        run_id,
        initial_context=execution.initial_context,
        usage_info=merged_usage_info,
        gathered_context=execution.gathered_context,
        state=execution.state,
        is_completed=execution.is_completed,
    )

    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    return _build_response(text_session)


@router.post(
    "/{workflow_id}/text-chat/sessions",
    response_model=WorkflowRunTextSessionResponse,
)
async def create_text_chat_session(
    workflow_id: int,
    request: CreateTextChatSessionRequest,
    user: UserModel = Depends(get_user),
) -> WorkflowRunTextSessionResponse:
    session_name = request.name or f"WR-TEXT-{uuid4().hex[:6].upper()}"
    try:
        workflow_run = await db_client.create_workflow_run(
            name=session_name,
            workflow_id=workflow_id,
            mode=WorkflowRunMode.TEXTCHAT.value,
            user_id=user.id,
            initial_context=request.initial_context,
            use_draft=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

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
        session_data=_default_session_data(),
        checkpoint=_default_checkpoint(),
    )
    session_data = _normalize_session_data(text_session.session_data)
    checkpoint = _normalize_checkpoint(text_session.checkpoint)

    session_data["turns"] = [_build_pending_turn(user_text=None)]
    session_data["status"] = "pending_assistant_turn"
    checkpoint["anchor_turn_id"] = _latest_completed_turn_id(session_data["turns"])

    try:
        await db_client.update_workflow_run_text_session(
            workflow_run.id,
            session_data=session_data,
            checkpoint=checkpoint,
            expected_revision=text_session.revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    text_session = await _load_text_session_or_404(workflow_id, workflow_run.id, user)
    return await _execute_pending_turn_and_build_response(
        workflow_id=workflow_id,
        run_id=workflow_run.id,
        text_session=text_session,
        user=user,
    )


@router.get(
    "/{workflow_id}/text-chat/sessions/{run_id}",
    response_model=WorkflowRunTextSessionResponse,
)
async def get_text_chat_session(
    workflow_id: int,
    run_id: int,
    user: UserModel = Depends(get_user),
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
    user: UserModel = Depends(get_user),
) -> WorkflowRunTextSessionResponse:
    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    session_data = _normalize_session_data(text_session.session_data)
    checkpoint = _normalize_checkpoint(text_session.checkpoint)

    active_turns, discarded_future = _truncate_future_turns(session_data)
    active_turns.append(_build_pending_turn(user_text=request.text))

    session_data["turns"] = active_turns
    session_data["discarded_future"] = discarded_future
    session_data["cursor_turn_id"] = None
    session_data["status"] = "pending_assistant_turn"
    checkpoint["anchor_turn_id"] = _latest_completed_turn_id(active_turns)

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=session_data,
            checkpoint=checkpoint,
            expected_revision=request.expected_revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise HTTPException(status_code=409, detail=_revision_conflict_detail(e))

    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    return await _execute_pending_turn_and_build_response(
        workflow_id=workflow_id,
        run_id=run_id,
        text_session=text_session,
        user=user,
    )


@router.post(
    "/{workflow_id}/text-chat/sessions/{run_id}/rewind",
    response_model=WorkflowRunTextSessionResponse,
)
async def rewind_text_chat_session(
    workflow_id: int,
    run_id: int,
    request: RewindTextChatSessionRequest,
    user: UserModel = Depends(get_user),
) -> WorkflowRunTextSessionResponse:
    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    session_data = _normalize_session_data(text_session.session_data)
    _validate_turn_cursor(session_data, request.cursor_turn_id)

    session_data["cursor_turn_id"] = request.cursor_turn_id
    session_data["status"] = "rewound" if request.cursor_turn_id else "idle"

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=session_data,
            expected_revision=request.expected_revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Text chat session revision conflict",
                "expected_revision": e.expected_revision,
                "actual_revision": e.actual_revision,
            },
        )

    text_session = await _load_text_session_or_404(workflow_id, run_id, user)
    return _build_response(text_session)
