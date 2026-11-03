import asyncio
import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.db.workflow_run_text_session_client import (
    WorkflowRunTextSessionRevisionConflictError,
)
from api.routes.workflow_text_chat import _stream_pending_turn_response
from api.services.workflow import text_chat_session_service, text_chat_stream
from api.services.workflow.text_chat_stream import TextChatTurnStream
from api.tasks import text_chat_completion
from api.tasks.function_names import FunctionNames


def session():
    return SimpleNamespace(
        workflow_run_id=42,
        revision=1,
        created_at=datetime.now(UTC),
        updated_at=None,
        session_data={"turns": [{"id": "turn-1", "status": "pending", "events": []}]},
        checkpoint={},
        workflow_run=SimpleNamespace(
            id=42,
            workflow_id=3,
            name="Test",
            mode="textchat",
            state="running",
            is_completed=False,
            initial_context={},
            gathered_context={},
            annotations={},
        ),
    )


@pytest.mark.asyncio
async def test_stream_delivers_announcement_before_execution_finishes(monkeypatch):
    release = asyncio.Event()
    completed = session()

    async def execute(**kwargs):
        kwargs["on_event"](
            {
                "type": "bot_speech",
                "created_at": datetime.now(UTC).isoformat(),
                "payload": {"text": "Please wait."},
            }
        )
        await asyncio.wait_for(release.wait(), timeout=5)
        return completed

    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    response = _stream_pending_turn_response(workflow_id=3, text_session=session())
    iterator = response.body_iterator
    try:
        initial = await asyncio.wait_for(anext(iterator), timeout=2)
        assert '"type":"session"' in initial
        announcement = await asyncio.wait_for(anext(iterator), timeout=2)
        assert "Please wait." in announcement
        assert not release.is_set()
        release.set()
        final = await asyncio.wait_for(anext(iterator), timeout=2)
        assert '"type":"complete"' in final
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["x-workflow-run-id"] == "42"
    finally:
        release.set()
        await iterator.aclose()


@pytest.mark.asyncio
async def test_disconnect_does_not_cancel_or_reexecute_accepted_turn(monkeypatch):
    release = asyncio.Event()
    saved = asyncio.Event()
    calls = 0

    async def execute(**kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_event"]({"type": "bot_speech", "payload": {"text": "Wait"}})
        await asyncio.wait_for(release.wait(), timeout=5)
        saved.set()
        return session()

    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=session())
    iterator = aiter(stream)
    try:
        assert (await asyncio.wait_for(anext(iterator), timeout=2)).kind == "event"
        await iterator.aclose()
        assert not stream.task.cancelled()
        release.set()
        await asyncio.wait_for(stream.task, timeout=2)
        assert saved.is_set()
        assert calls == 1
    finally:
        release.set()
        await asyncio.wait_for(stream.task, timeout=2)


@pytest.mark.asyncio
async def test_execution_failure_is_a_terminal_stream_event(monkeypatch):
    async def execute(**kwargs):
        raise RuntimeError("Tool failed")

    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=session())
    updates = await asyncio.wait_for(_collect(stream), timeout=2)
    assert len(updates) == 1
    assert updates[0].kind == "error"
    assert str(updates[0].error) == "Tool failed"


async def _collect(stream):
    return [update async for update in stream]


@pytest.mark.asyncio
async def test_shutdown_allows_a_turn_to_finish_during_grace(monkeypatch):
    saved = asyncio.Event()

    async def execute(**kwargs):
        await asyncio.sleep(0)
        saved.set()
        return session()

    fail = AsyncMock()
    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    monkeypatch.setattr(text_chat_stream, "_mark_pending_turn_failed", fail)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=session())
    stream.disconnect()
    await asyncio.wait_for(text_chat_stream.finish_streaming_text_chat_turns(), 2)
    assert saved.is_set()
    assert not stream.task.cancelled()
    fail.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_persists_failure_before_slow_cancellation_cleanup(monkeypatch):
    started = asyncio.Event()
    release_cleanup = asyncio.Event()
    saved = AsyncMock()
    event = {"type": "bot_speech", "payload": {"text": "Checking."}}

    async def execute(**kwargs):
        kwargs["on_event"](event)
        started.set()
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 5)
        except asyncio.CancelledError:
            # Even a blocked tool cleanup cannot keep the session pending.
            saved.assert_awaited_once()
            await asyncio.wait_for(release_cleanup.wait(), 5)
            raise

    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    monkeypatch.setattr(
        text_chat_session_service.db_client, "update_workflow_run_text_session", saved
    )
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_TURN_GRACE_SECONDS", 0)
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_CANCEL_SECONDS", 0)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=session())
    stream.disconnect()
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(text_chat_stream.finish_streaming_text_chat_turns(), 2)
        stored = saved.await_args.kwargs["session_data"]
        assert stored["status"] == "error"
        assert stored["turns"][-1]["status"] == "failed"
        assert stored["turns"][-1]["events"][0] == event
        assert stored["turns"][-1]["events"][-1]["type"] == "execution_error"
        assert saved.await_args.kwargs["expected_revision"] == 1
    finally:
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stream.task, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_step", ["upload", "enqueue"])
async def test_shutdown_hands_off_committed_turn_before_cancelling(
    monkeypatch, blocked_step
):
    """Exercise the real service across commit, revision conflict, and handoff."""
    from api.tasks import arq

    accepted = session()
    accepted.workflow_run.usage_info = {}
    completed = copy.deepcopy(accepted)
    committed = False
    blocked = asyncio.Event()
    release = asyncio.Event()
    durable_jobs = []
    upload_calls = 0
    enqueue_calls = 0
    execution = SimpleNamespace(
        assistant_text="Goodbye",
        assistant_created_at=datetime.now(UTC).isoformat(),
        events=[{"type": "bot_speech", "payload": {"text": "Goodbye"}}],
        checkpoint={},
        usage={},
        initial_context={},
        gathered_context={},
        state="completed",
        is_completed=True,
    )
    execute = AsyncMock(return_value=execution)

    async def commit(run_id, **kwargs):
        nonlocal committed
        completed.revision = accepted.revision + 1
        completed.session_data = kwargs["session_data"]
        completed.workflow_run.is_completed = True
        committed = True

    async def update(run_id, **kwargs):
        assert committed
        raise WorkflowRunTextSessionRevisionConflictError(
            expected_revision=kwargs["expected_revision"],
            actual_revision=completed.revision,
        )

    async def upload(*args):
        nonlocal upload_calls
        upload_calls += 1
        if blocked_step == "upload" and upload_calls == 1:
            blocked.set()
            await asyncio.wait_for(release.wait(), 5)

    async def enqueue(function, *args, **kwargs):
        nonlocal enqueue_calls
        if function == FunctionNames.PROCESS_WORKFLOW_COMPLETION:
            enqueue_calls += 1
            if blocked_step == "enqueue" and enqueue_calls == 1:
                blocked.set()
                await asyncio.wait_for(release.wait(), 5)
        durable_jobs.append((function, args, kwargs))

    monkeypatch.setattr(
        text_chat_session_service, "execute_text_chat_pending_turn", execute
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client,
        "complete_workflow_run_text_session",
        commit,
    )
    monkeypatch.setattr(
        text_chat_session_service.db_client, "update_workflow_run_text_session", update
    )
    monkeypatch.setattr(
        text_chat_session_service,
        "_reload_text_chat_session",
        AsyncMock(return_value=completed),
    )
    monkeypatch.setattr(
        text_chat_session_service, "_upload_text_chat_transcript", upload
    )
    monkeypatch.setattr(arq, "enqueue_job", enqueue)
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_TURN_GRACE_SECONDS", 0)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=accepted)
    stream.disconnect()
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        await asyncio.wait_for(text_chat_stream.finish_streaming_text_chat_turns(), 2)
        assert stream.task.cancelled()
        assert completed.session_data["turns"][-1]["status"] == "completed"
        assert durable_jobs == [
            (
                FunctionNames.FINALIZE_COMPLETED_TEXT_CHAT,
                (42,),
                {
                    "_job_id": "text-chat-finalization-42",
                },
            )
        ]

        # A different worker can finish using only the persisted session/run.
        await asyncio.wait_for(
            text_chat_completion.finalize_completed_text_chat({}, 42), 2
        )
        assert upload_calls == 2
        assert durable_jobs[-1] == (
            FunctionNames.PROCESS_WORKFLOW_COMPLETION,
            (42,),
            {
                "_job_id": "workflow-completion-42",
            },
        )
        execute.assert_awaited_once()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(stream.task, return_exceptions=True), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["save", "timeout", "handoff", "live_session"])
async def test_shutdown_does_not_cancel_without_a_saved_failure_or_handoff(
    monkeypatch, failure
):
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(**kwargs):
        started.set()
        await asyncio.wait_for(release.wait(), 5)
        return session()

    async def fail(**kwargs):
        if failure == "save":
            raise RuntimeError("Database unavailable")
        if failure == "timeout":
            await asyncio.wait_for(release.wait(), 5)
        return False

    handoff = AsyncMock(return_value=None)
    if failure == "handoff":
        handoff.side_effect = RuntimeError("Redis unavailable")
    monkeypatch.setattr(text_chat_stream, "execute_pending_text_chat_turn", execute)
    monkeypatch.setattr(text_chat_stream, "_mark_pending_turn_failed", fail)
    monkeypatch.setattr(text_chat_stream, "hand_off_completed_text_chat", handoff)
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_TURN_GRACE_SECONDS", 0)
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_SAVE_SECONDS", 0.01)
    monkeypatch.setattr(text_chat_stream, "SHUTDOWN_CANCEL_SECONDS", 0)
    stream = TextChatTurnStream(workflow_id=3, run_id=42, text_session=session())
    try:
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(text_chat_stream.finish_streaming_text_chat_turns(), 2)
        assert not stream.task.done()
        assert not stream.task.cancelling()
        assert stream._queue.empty()
        if failure in {"save", "timeout"}:
            handoff.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(stream.task, 2)


@pytest.mark.asyncio
async def test_finalization_worker_retries_transient_failure(monkeypatch):
    from arq import Retry

    monkeypatch.setattr(
        text_chat_completion,
        "finalize_session",
        AsyncMock(side_effect=RuntimeError("Redis unavailable")),
    )
    with pytest.raises(Retry):
        await text_chat_completion.finalize_completed_text_chat({}, 42)
