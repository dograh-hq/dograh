from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import api.services.workflow.text_chat_session_service as text_chat_session_service
from api.db.models import WorkflowRunTextSessionModel
from api.services.workflow.text_chat_session_service import (
    TextChatSessionExecutionError,
    TextChatTurnNotFoundError,
    _enqueue_text_chat_completion,
    _reload_text_chat_session,
    _text_chat_duration_seconds,
    _upload_text_chat_transcript,
    build_pending_text_chat_turn,
    complete_text_chat_session,
    execute_pending_text_chat_turn,
    truncate_text_chat_future_turns,
    validate_text_chat_turn_cursor,
)
from api.tasks.function_names import FunctionNames


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
async def test_completed_pending_turn_enqueues_workflow_completion(monkeypatch):
    started_at = datetime.now(UTC) - timedelta(minutes=5)
    session = SimpleNamespace(
        revision=7,
        created_at=started_at,
        session_data={
            "status": "pending_assistant_turn",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "pending",
                    "created_at": "2026-08-05T00:00:00+00:00",
                    "user_message": {
                        "text": "Goodbye",
                        "created_at": "2026-08-05T00:00:00+00:00",
                    },
                    "assistant_message": None,
                    "events": [],
                    "usage": {},
                }
            ],
        },
        checkpoint={},
        workflow_run=SimpleNamespace(
            usage_info={},
            is_completed=False,
            created_at=started_at,
        ),
    )
    execution = SimpleNamespace(
        assistant_text="Goodbye!",
        assistant_created_at="2026-08-05T00:00:01+00:00",
        events=[],
        usage={"call_duration_seconds": 1},
        checkpoint={"current_node_id": "end"},
        initial_context={},
        gathered_context={"call_disposition": "end_call_tool"},
        state="completed",
        is_completed=True,
    )
    reloaded = SimpleNamespace(workflow_run=SimpleNamespace(is_completed=True))
    complete_session = AsyncMock()
    enqueue_completion = AsyncMock()
    upload_transcript = AsyncMock()

    monkeypatch.setattr(
        text_chat_session_service,
        "execute_text_chat_pending_turn",
        AsyncMock(return_value=execution),
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "complete_workflow_run_text_session",
        complete_session,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_enqueue_text_chat_completion",
        enqueue_completion,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_upload_text_chat_transcript",
        upload_transcript,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_reload_text_chat_session",
        AsyncMock(return_value=reloaded),
    )

    result = await execute_pending_text_chat_turn(
        workflow_id=3,
        run_id=42,
        text_session=session,
    )

    assert result is reloaded
    persisted_session = complete_session.await_args.kwargs["session_data"]
    assert persisted_session["status"] == "completed"
    assert persisted_session["turns"][-1]["status"] == "completed"
    assert complete_session.await_args.kwargs["state"] == "completed"
    duration = complete_session.await_args.kwargs["usage_info"]["call_duration_seconds"]
    assert 300 <= duration < 310
    upload_transcript.assert_awaited_once()
    enqueue_completion.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_complete_text_chat_session_marks_user_hangup_and_enqueues(monkeypatch):
    started_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    completed_at = started_at + timedelta(minutes=7, seconds=30)
    workflow_run = SimpleNamespace(
        is_completed=False,
        gathered_context={"call_tags": ["existing"]},
        usage_info={"llm": {"prompt_tokens": 12}},
        created_at=started_at,
    )
    session = SimpleNamespace(
        revision=4,
        created_at=started_at,
        session_data={"status": "idle", "turns": []},
        workflow_run=workflow_run,
    )
    reloaded = SimpleNamespace(workflow_run=SimpleNamespace(is_completed=True))
    complete_session = AsyncMock()
    enqueue_completion = AsyncMock()
    upload_transcript = AsyncMock()

    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "complete_workflow_run_text_session",
        complete_session,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_enqueue_text_chat_completion",
        enqueue_completion,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_upload_text_chat_transcript",
        upload_transcript,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_reload_text_chat_session",
        AsyncMock(return_value=reloaded),
    )

    result = await complete_text_chat_session(
        run_id=42,
        text_session=session,
        expected_revision=4,
        completed_at=completed_at,
    )

    assert result is reloaded
    complete_session.assert_awaited_once()
    assert complete_session.await_args.kwargs["session_data"]["status"] == "completed"
    assert complete_session.await_args.kwargs["expected_revision"] == 4
    update = complete_session.await_args.kwargs
    assert update["state"] == "completed"
    assert update["gathered_context"] == {
        "call_disposition": "user_hangup",
        "mapped_call_disposition": "user_hangup",
        "call_tags": ["existing", "user_hangup"],
    }
    assert update["usage_info"] == {
        "llm": {"prompt_tokens": 12},
        "call_duration_seconds": 450.0,
    }
    assert update["logs"] == {"realtime_feedback_events": []}
    upload_transcript.assert_awaited_once_with(42, [])
    enqueue_completion.assert_awaited_once_with(42)


def test_text_chat_duration_uses_session_wall_clock_lifetime():
    started_at = datetime(2026, 8, 5, 10, 15, tzinfo=UTC)
    session = SimpleNamespace(
        created_at=started_at,
        workflow_run=SimpleNamespace(created_at=started_at - timedelta(seconds=10)),
    )

    duration = _text_chat_duration_seconds(
        session,
        completed_at=started_at + timedelta(minutes=3, seconds=12),
    )

    assert duration == 192.0


@pytest.mark.asyncio
async def test_upload_text_chat_transcript_persists_rendered_text(monkeypatch):
    upload_artifacts = AsyncMock()
    monkeypatch.setattr(
        text_chat_session_service,
        "upload_workflow_run_artifacts",
        upload_artifacts,
    )
    events = [
        {
            "type": "rtf-user-transcription",
            "payload": {
                "text": "Hello",
                "final": True,
                "timestamp": "2026-08-05T10:00:00+00:00",
            },
        },
        {
            "type": "rtf-bot-text",
            "payload": {
                "text": "Hi there",
                "timestamp": "2026-08-05T10:00:01+00:00",
            },
        },
    ]

    await _upload_text_chat_transcript(42, events)

    upload_artifacts.assert_awaited_once_with(
        42,
        transcript_text=(
            "[2026-08-05T10:00:00+00:00] user: Hello\n"
            "[2026-08-05T10:00:01+00:00] assistant: Hi there\n"
        ),
    )


@pytest.mark.asyncio
async def test_complete_text_chat_session_retries_enqueue_for_completed_run(
    monkeypatch,
):
    session = SimpleNamespace(
        workflow_run=SimpleNamespace(is_completed=True),
    )
    reloaded = SimpleNamespace(workflow_run=SimpleNamespace(is_completed=True))
    enqueue_completion = AsyncMock()
    update_text_session = AsyncMock()
    update_workflow_run = AsyncMock()
    complete_session = AsyncMock()

    monkeypatch.setattr(
        text_chat_session_service,
        "_enqueue_text_chat_completion",
        enqueue_completion,
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_reload_text_chat_session",
        AsyncMock(return_value=reloaded),
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "update_workflow_run_text_session",
        update_text_session,
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "update_workflow_run",
        update_workflow_run,
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "complete_workflow_run_text_session",
        complete_session,
    )

    result = await complete_text_chat_session(
        run_id=42,
        text_session=session,
        expected_revision=4,
    )

    assert result is reloaded
    enqueue_completion.assert_awaited_once_with(42)
    update_text_session.assert_not_awaited()
    update_workflow_run.assert_not_awaited()
    complete_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_text_chat_completion_uses_deterministic_job_id(monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setattr("api.tasks.arq.enqueue_job", enqueue)

    await _enqueue_text_chat_completion(42)

    enqueue.assert_awaited_once_with(
        FunctionNames.PROCESS_WORKFLOW_COMPLETION,
        42,
        _job_id="workflow-completion-42",
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
