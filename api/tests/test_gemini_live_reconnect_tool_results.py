import json
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
        self.close = AsyncMock()


def _make_service() -> _TestDograhGeminiLiveLLMService:
    service = _TestDograhGeminiLiveLLMService(api_key="test-key")
    service.stop_all_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()
    service.cancel_task = AsyncMock()
    service.push_error = AsyncMock()
    return service


def _make_tool_result_context(tool_call_id: str) -> LLMContext:
    return LLMContext(
        messages=[
            {
                "role": "tool",
                "content": json.dumps({"status": "done"}),
                "tool_call_id": tool_call_id,
            }
        ]
    )


@pytest.mark.asyncio
async def test_updated_context_during_reconnect_keeps_result_pending_until_session_ready():
    service = _make_service()
    service._handled_initial_context = True
    service._tool_call_id_to_name = {"call-transition": "transition_to_next_node"}
    service._session = _FakeSession()

    context = _make_tool_result_context("call-transition")

    await service._disconnect()
    await service._handle_context(context)

    # A reconnect gap should not count as successful delivery to Gemini.
    assert "call-transition" not in service._completed_tool_calls

    session = _FakeSession()
    await service._handle_session_ready(session)

    session.send_tool_response.assert_awaited_once()
    sent_response = session.send_tool_response.await_args.kwargs["function_responses"]
    assert sent_response.id == "call-transition"
    assert sent_response.name == "transition_to_next_node"
    assert "call-transition" in service._completed_tool_calls


@pytest.mark.asyncio
async def test_disconnect_does_not_forget_previously_delivered_tool_results():
    service = _make_service()
    service._context = _make_tool_result_context("call-transition")
    service._completed_tool_calls = {"call-transition"}
    service._tool_call_id_to_name = {"call-transition": "transition_to_next_node"}
    service._session = _FakeSession()
    service._tool_result = AsyncMock()

    await service._disconnect()
    await service._process_completed_function_calls(send_new_results=True)

    service._tool_result.assert_not_awaited()
    assert service._completed_tool_calls == {"call-transition"}
