"""Service helpers for text-chat session lifecycle orchestration."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from api.db import db_client
from api.db.models import WorkflowRunTextSessionModel
from api.db.workflow_run_text_session_client import (
    WorkflowRunTextSessionRevisionConflictError,
)
from api.services.workflow.text_chat_logs import (
    build_text_chat_realtime_feedback_events,
)
from api.services.workflow.text_chat_runner import (
    default_text_chat_checkpoint,
    execute_text_chat_pending_turn,
    merge_text_chat_usage_info,
    normalize_text_chat_checkpoint,
)

TEXT_CHAT_SESSION_VERSION = 1


class TextChatSessionRevisionConflictError(Exception):
    def __init__(self, expected_revision: int, actual_revision: int):
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "Text chat session revision conflict: "
            f"expected {expected_revision}, found {actual_revision}"
        )


class TextChatSessionExecutionError(Exception):
    """Raised when the assistant turn fails to execute."""


class TextChatPendingTurnLostError(Exception):
    """Raised when the pending turn disappears before persistence completes."""


class TextChatTurnNotFoundError(Exception):
    """Raised when a requested rewind cursor does not exist in the session."""


def default_text_chat_session_data() -> dict[str, Any]:
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


def normalize_text_chat_session_data(
    session_data: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = {
        **default_text_chat_session_data(),
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


async def initialize_text_chat_session(
    *,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
) -> WorkflowRunTextSessionModel:
    session_data = normalize_text_chat_session_data(text_session.session_data)
    checkpoint = normalize_text_chat_checkpoint(text_session.checkpoint)

    session_data["turns"] = [build_pending_text_chat_turn(user_text=None)]
    session_data["status"] = "pending_assistant_turn"
    checkpoint["anchor_turn_id"] = latest_completed_text_chat_turn_id(
        session_data["turns"]
    )

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=session_data,
            checkpoint=checkpoint,
            expected_revision=text_session.revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise TextChatSessionRevisionConflictError(
            expected_revision=e.expected_revision,
            actual_revision=e.actual_revision,
        ) from e

    return await _reload_text_chat_session(run_id)


async def append_text_chat_user_message(
    *,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
    user_text: str,
    expected_revision: int | None,
) -> WorkflowRunTextSessionModel:
    session_data = normalize_text_chat_session_data(text_session.session_data)
    checkpoint = normalize_text_chat_checkpoint(text_session.checkpoint)

    active_turns, discarded_future = truncate_text_chat_future_turns(session_data)
    active_turns.append(build_pending_text_chat_turn(user_text=user_text))

    session_data["turns"] = active_turns
    session_data["discarded_future"] = discarded_future
    session_data["cursor_turn_id"] = None
    session_data["status"] = "pending_assistant_turn"
    checkpoint["anchor_turn_id"] = latest_completed_text_chat_turn_id(active_turns)

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=session_data,
            checkpoint=checkpoint,
            expected_revision=expected_revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise TextChatSessionRevisionConflictError(
            expected_revision=e.expected_revision,
            actual_revision=e.actual_revision,
        ) from e

    return await _reload_text_chat_session(run_id)


async def rewind_text_chat_session_state(
    *,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
    cursor_turn_id: str | None,
    expected_revision: int | None,
) -> WorkflowRunTextSessionModel:
    session_data = normalize_text_chat_session_data(text_session.session_data)
    validate_text_chat_turn_cursor(session_data, cursor_turn_id)

    session_data["cursor_turn_id"] = cursor_turn_id
    session_data["status"] = "rewound" if cursor_turn_id else "idle"

    try:
        await db_client.update_workflow_run_text_session(
            run_id,
            session_data=session_data,
            expected_revision=expected_revision,
        )
    except WorkflowRunTextSessionRevisionConflictError as e:
        raise TextChatSessionRevisionConflictError(
            expected_revision=e.expected_revision,
            actual_revision=e.actual_revision,
        ) from e

    await db_client.update_workflow_run(
        run_id,
        logs={
            "realtime_feedback_events": build_text_chat_realtime_feedback_events(
                session_data
            )
        },
    )

    return await _reload_text_chat_session(run_id)


async def execute_pending_text_chat_turn(
    *,
    workflow_id: int,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
) -> WorkflowRunTextSessionModel:
    """Execute the current pending assistant turn and persist its side effects."""
    session_data = normalize_text_chat_session_data(text_session.session_data)
    checkpoint = normalize_text_chat_checkpoint(text_session.checkpoint)

    try:
        execution = await execute_text_chat_pending_turn(
            workflow_run_id=run_id,
            workflow_id=workflow_id,
            session_data=session_data,
            checkpoint=checkpoint,
        )
    except Exception as e:
        await _mark_pending_turn_failed(
            run_id=run_id,
            text_session=text_session,
            error_message=str(e),
        )
        raise TextChatSessionExecutionError(
            f"Failed to execute text chat assistant turn: {e}"
        ) from e

    completed_session_data = normalize_text_chat_session_data(text_session.session_data)
    completed_turns = list(completed_session_data.get("turns") or [])
    if not completed_turns or completed_turns[-1].get("status") != "pending":
        raise TextChatPendingTurnLostError(
            "Text chat session lost its pending turn before completion"
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
        raise TextChatSessionRevisionConflictError(
            expected_revision=e.expected_revision,
            actual_revision=e.actual_revision,
        ) from e

    existing_usage_info = text_session.workflow_run.usage_info or {}
    merged_usage_info = merge_text_chat_usage_info(existing_usage_info, execution.usage)
    text_chat_logs = {
        "realtime_feedback_events": build_text_chat_realtime_feedback_events(
            completed_session_data
        )
    }
    await db_client.update_workflow_run(
        run_id,
        initial_context=execution.initial_context,
        usage_info=merged_usage_info,
        gathered_context=execution.gathered_context,
        logs=text_chat_logs,
        state=execution.state,
        is_completed=execution.is_completed,
    )

    return await _reload_text_chat_session(run_id)


def validate_text_chat_turn_cursor(
    session_data: dict[str, Any],
    cursor_turn_id: str | None,
) -> None:
    if cursor_turn_id is None:
        return
    if not any(turn.get("id") == cursor_turn_id for turn in session_data["turns"]):
        raise TextChatTurnNotFoundError("Turn not found in text chat session")


def truncate_text_chat_future_turns(
    session_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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

    raise TextChatTurnNotFoundError("Turn not found in text chat session")


def latest_completed_text_chat_turn_id(turns: list[dict[str, Any]]) -> str | None:
    for turn in reversed(turns):
        if turn.get("status") == "completed":
            return turn.get("id")
    return None


def build_pending_text_chat_turn(*, user_text: str | None) -> dict[str, Any]:
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


async def _mark_pending_turn_failed(
    *,
    run_id: int,
    text_session: WorkflowRunTextSessionModel,
    error_message: str,
) -> None:
    failed_session_data = normalize_text_chat_session_data(text_session.session_data)
    failed_turns = list(failed_session_data.get("turns") or [])
    if not failed_turns or failed_turns[-1].get("status") != "pending":
        return

    failed_turns[-1]["status"] = "failed"
    failed_turns[-1]["events"] = [
        *(failed_turns[-1].get("events") or []),
        {
            "type": "execution_error",
            "created_at": datetime.now(UTC).isoformat(),
            "payload": {"message": error_message},
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
        return


async def _reload_text_chat_session(run_id: int) -> WorkflowRunTextSessionModel:
    organization_id = await db_client.get_organization_id_by_workflow_run_id(run_id)
    if organization_id is None:
        raise TextChatSessionExecutionError(
            "Workflow run organization not found after update"
        )
    updated_text_session = await db_client.get_workflow_run_text_session(
        run_id,
        organization_id=organization_id,
    )
    if updated_text_session is None:
        raise TextChatSessionExecutionError("Text chat session not found after update")
    return updated_text_session


__all__ = [
    "TEXT_CHAT_SESSION_VERSION",
    "TextChatTurnNotFoundError",
    "append_text_chat_user_message",
    "build_pending_text_chat_turn",
    "TextChatPendingTurnLostError",
    "TextChatSessionExecutionError",
    "TextChatSessionRevisionConflictError",
    "default_text_chat_checkpoint",
    "default_text_chat_session_data",
    "execute_pending_text_chat_turn",
    "initialize_text_chat_session",
    "latest_completed_text_chat_turn_id",
    "normalize_text_chat_checkpoint",
    "normalize_text_chat_session_data",
    "rewind_text_chat_session_state",
    "truncate_text_chat_future_turns",
    "validate_text_chat_turn_cursor",
]
