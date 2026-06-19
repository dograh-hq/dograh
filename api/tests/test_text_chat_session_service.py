from unittest.mock import AsyncMock

import pytest

import api.services.workflow.text_chat_session_service as text_chat_session_service
from api.db.models import WorkflowRunTextSessionModel
from api.services.workflow.text_chat_session_service import (
    TextChatSessionExecutionError,
    TextChatTurnNotFoundError,
    _reload_text_chat_session,
    build_pending_text_chat_turn,
    execute_pending_text_chat_turn,
    truncate_text_chat_future_turns,
    validate_text_chat_turn_cursor,
)


def test_build_pending_text_chat_turn_sets_pending_shape():
    turn = build_pending_text_chat_turn(user_text="Hello")

    assert turn["id"].startswith("turn_")
    assert turn["status"] == "pending"
    assert turn["user_message"]["text"] == "Hello"
    assert turn["assistant_message"] is None
    assert turn["events"] == []
    assert turn["usage"] == {}


def test_truncate_text_chat_future_turns_moves_rewound_branch_to_discarded_future():
    session_data = {
        "cursor_turn_id": "turn-2",
        "turns": [
            {"id": "turn-1"},
            {"id": "turn-2"},
            {"id": "turn-3"},
        ],
        "discarded_future": [],
    }

    active_turns, discarded_future = truncate_text_chat_future_turns(session_data)

    assert active_turns == [{"id": "turn-1"}, {"id": "turn-2"}]
    assert discarded_future[0]["rewound_from_turn_id"] == "turn-2"
    assert discarded_future[0]["turns"] == [{"id": "turn-3"}]


def test_validate_text_chat_turn_cursor_raises_for_missing_turn():
    with pytest.raises(TextChatTurnNotFoundError):
        validate_text_chat_turn_cursor(
            {"turns": [{"id": "turn-1"}]},
            "turn-404",
        )


@pytest.mark.asyncio
async def test_reload_text_chat_session_uses_run_id_to_resolve_organization(
    monkeypatch,
):
    reloaded_session = WorkflowRunTextSessionModel(workflow_run_id=123)
    get_org_id = AsyncMock(return_value=77)
    get_text_session = AsyncMock(return_value=reloaded_session)

    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "get_organization_id_by_workflow_run_id",
        get_org_id,
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "get_workflow_run_text_session",
        get_text_session,
    )

    result = await _reload_text_chat_session(123)

    assert result is reloaded_session
    get_org_id.assert_awaited_once_with(123)
    get_text_session.assert_awaited_once_with(123, organization_id=77)


@pytest.mark.asyncio
async def test_execute_pending_turn_surfaces_original_exception_message(monkeypatch):
    session = WorkflowRunTextSessionModel(workflow_run_id=42)
    session.session_data = {
        "turns": [{"id": "turn-1", "status": "pending"}],
        "cursor_turn_id": "turn-1",
    }
    session.checkpoint = None

    monkeypatch.setattr(
        text_chat_session_service,
        "execute_text_chat_pending_turn",
        AsyncMock(side_effect=RuntimeError("Workflow has 2 start nodes")),
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_mark_pending_turn_failed",
        AsyncMock(),
    )

    with pytest.raises(
        TextChatSessionExecutionError, match="Workflow has 2 start nodes"
    ):
        await execute_pending_text_chat_turn(
            workflow_id=1,
            run_id=42,
            text_session=session,
        )


@pytest.mark.asyncio
async def test_reload_text_chat_session_raises_when_run_organization_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "get_organization_id_by_workflow_run_id",
        AsyncMock(return_value=None),
    )

    with pytest.raises(TextChatSessionExecutionError, match="organization not found"):
        await _reload_text_chat_session(123)
