"""Regression tests for the reconnect-history-seed patch in
DograhGeminiLiveLLMService._handle_session_ready.

When a node transition swaps system_instruction, pipecat triggers a Gemini
Live reconnect. If the new session has no session_resumption_handle (common
for quick transitions before the server has issued one), the server-side
session has zero conversation history. Without seeding, the LLM in the new
node has no idea what the user just said and responds with things like
"I haven't heard anything from you yet".

These tests cover the patch in `gemini_live.py:_handle_session_ready` that
calls `_create_initial_response(for_reconnect=True)` to seed the new session
with the client-side conversation history.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService


class _TestDograhGeminiLiveLLMService(DograhGeminiLiveLLMService):
    """Dograh Gemini service with client creation stubbed for unit tests."""

    def create_client(self):
        self._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=None))
        )


class _FakeSession:
    def __init__(self):
        self.send_tool_response = AsyncMock()
        self.send_realtime_input = AsyncMock()
        self.send_client_content = AsyncMock()
        self.close = AsyncMock()


def _make_service() -> _TestDograhGeminiLiveLLMService:
    service = _TestDograhGeminiLiveLLMService(api_key="test-key")
    service.stop_all_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()
    service.cancel_task = AsyncMock()
    service.push_error = AsyncMock()
    service._create_initial_response = AsyncMock()
    service._drain_pending_tool_results = AsyncMock()
    return service


def _make_user_context(user_text: str) -> LLMContext:
    return LLMContext(
        messages=[
            {"role": "user", "content": user_text},
        ]
    )


@pytest.mark.asyncio
async def test_reconnect_without_resumption_handle_seeds_history():
    """The bug: a reconnect with no session_resumption_handle leaves the
    new Gemini Live session with no history, so the LLM doesn't see the
    user's last message. The fix: call _create_initial_response(for_reconnect=True)
    on session-ready to seed the history."""
    service = _make_service()
    service._handled_initial_context = True
    service._session_resumption_handle = None
    service._run_llm_when_session_ready = False
    service._context = _make_user_context("Can you tell me more about Isaiah?")

    await service._handle_session_ready(_FakeSession())

    service._create_initial_response.assert_awaited_once_with(for_reconnect=True)


@pytest.mark.asyncio
async def test_handle_changed_settings_discards_resumption_handle_before_reconnect():
    """Node-transition reconnects must clear session_resumption_handle so the
    new session is fresh (and _handle_session_ready can fully seed it).

    Without this, the handle restores state from BEFORE the user's last
    message — the LLM in the new node then says "I haven't heard anything".
    """
    service = _make_service()
    service._session = _FakeSession()
    service._bot_is_responding = False
    service._session_resumption_handle = "stale-handle-from-before-user-spoke"
    service._reconnect = AsyncMock()

    await service._handle_changed_settings({"system_instruction": "new prompt"})

    assert service._session_resumption_handle is None
    service._reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_deferred_reconnect_also_discards_resumption_handle():
    """When system_instruction changes mid-bot-turn, the reconnect is deferred
    until _set_bot_is_responding(False) fires. That deferred reconnect must
    also clear the handle for the same reason."""
    service = _make_service()
    service._bot_is_responding = True
    service._reconnect_pending = True
    service._session_resumption_handle = "stale-handle"
    service._reconnect = AsyncMock()
    service._run_pending_function_calls = AsyncMock()

    # Simulate bot finishing its turn
    await service._set_bot_is_responding(False)

    assert service._session_resumption_handle is None
    service._reconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_connect_uses_queued_initial_response_not_reconnect_seed():
    """On the very first connect (not a reconnect), the queued initial
    response path runs — not the new reconnect-seed branch."""
    service = _make_service()
    service._handled_initial_context = False
    service._run_llm_when_session_ready = True
    service._session_resumption_handle = None
    service._context = LLMContext(messages=[])

    await service._handle_session_ready(_FakeSession())

    # First-connect path: _create_initial_response() with no kwargs
    service._create_initial_response.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_session_ready_without_initial_context_or_queue_does_nothing_extra():
    """Defensive: if neither the queued-response flag nor the reconnect
    condition apply, _create_initial_response must not be called."""
    service = _make_service()
    service._handled_initial_context = False
    service._run_llm_when_session_ready = False
    service._session_resumption_handle = None
    service._context = LLMContext(messages=[])

    await service._handle_session_ready(_FakeSession())

    service._create_initial_response.assert_not_awaited()
    service._drain_pending_tool_results.assert_awaited_once()
