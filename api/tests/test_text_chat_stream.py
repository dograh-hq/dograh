import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api.routes.workflow_text_chat import _stream_pending_turn_response
from api.services.workflow import text_chat_stream
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
