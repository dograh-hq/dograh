import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService


class _TestDograhGeminiLiveLLMService(DograhGeminiLiveLLMService):
    """Dograh Gemini service with client creation stubbed for unit tests."""

    def create_client(self):
        self._client = SimpleNamespace(
            aio=SimpleNamespace(live=SimpleNamespace(connect=None))
        )


class _FakeSession:
    def __init__(self):
        self.send_client_content = AsyncMock()
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


@pytest.mark.asyncio
async def test_user_transcription_matches_upstream_upstream_push_behavior():
    service = _make_service()
    service._handle_user_transcription = AsyncMock()
    service.push_frame = AsyncMock()
    service.broadcast_frame = AsyncMock()

    await service._push_user_transcription("Hi there")

    service._handle_user_transcription.assert_awaited_once_with(
        "Hi there", True, service._settings.language
    )
    service.broadcast_frame.assert_not_awaited()
    service.push_frame.assert_awaited_once()

    frame, direction = service.push_frame.await_args.args
    assert isinstance(frame, TranscriptionFrame)
    assert frame.text == "Hi there"
    assert frame.finalized is False
    assert direction == FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_tts_greeting_sends_exact_static_greeting_prompt_to_gemini():
    service = _make_service()
    service._context = LLMContext()
    service._session = _FakeSession()

    await service.process_frame(
        TTSSpeakFrame("Hi Sam, this is Sarah from Acme.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._session.send_client_content.assert_awaited_once()
    kwargs = service._session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is True

    turns = kwargs["turns"]
    assert len(turns) == 1
    assert turns[0].role == "user"
    prompt = turns[0].parts[0].text
    assert "The phone call has just connected. Greet the caller now:" in prompt
    assert (
        'Do not add anything before or after it.\n\n"Hi Sam, this is Sarah from Acme."'
        in prompt
    )

    assert service._handled_initial_context is True
    assert service._pending_initial_greeting_text is None
    assert service._ready_for_realtime_input is True


@pytest.mark.asyncio
async def test_tts_greeting_waits_for_gemini_session_before_sending_prompt():
    service = _make_service()
    service._context = LLMContext()

    await service.process_frame(
        TTSSpeakFrame("Hello from Dograh.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    assert service._handled_initial_context is True
    assert service._run_llm_when_session_ready is True
    assert service._pending_initial_greeting_text == "Hello from Dograh."

    session = _FakeSession()
    await service._handle_session_ready(session)

    session.send_client_content.assert_awaited_once()
    prompt = session.send_client_content.await_args.kwargs["turns"][0].parts[0].text
    assert prompt.endswith('"Hello from Dograh."')
    assert service._run_llm_when_session_ready is False
    assert service._pending_initial_greeting_text is None
