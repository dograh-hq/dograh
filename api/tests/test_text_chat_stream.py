import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.routes.workflow_text_chat import _stream_pending_turn_response
from api.services.workflow import text_chat_session_service, text_chat_stream
from api.services.workflow.text_chat_stream import TextChatTurnStream


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
