from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openai.realtime import events

from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
)


def _make_service() -> DograhOpenAIRealtimeLLMService:
    service = DograhOpenAIRealtimeLLMService(api_key="test-key")
    service._create_response = AsyncMock()
    service._process_completed_function_calls = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_initial_context_triggers_response_when_context_was_prepopulated():
    service = _make_service()
    context = LLMContext()
    service._context = context

    await service._handle_context(context)

    assert service._handled_initial_context is True
    assert service._context is context
    service._create_response.assert_awaited_once()
    service._process_completed_function_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_updated_context_uses_tool_result_path_after_initial_context():
    service = _make_service()
    context = LLMContext()
    service._handled_initial_context = True

    await service._handle_context(context)

    assert service._context is context
    service._create_response.assert_not_awaited()
    service._process_completed_function_calls.assert_awaited_once_with(
        send_new_results=True
    )


@pytest.mark.asyncio
async def test_tts_greeting_sends_exact_static_greeting_prompt():
    service = _make_service()
    service._context = LLMContext()
    service._api_session_ready = True
    service.send_client_event = AsyncMock()
    service.push_frame = AsyncMock()
    service.start_processing_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()

    await service.process_frame(
        TTSSpeakFrame("Hi Sam, this is Sarah from Acme.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    sent_events = [call.args[0] for call in service.send_client_event.await_args_list]
    assert not any(
        isinstance(event, events.ConversationItemCreateEvent) for event in sent_events
    )
    assert isinstance(sent_events[0], events.SessionUpdateEvent)
    response_event = sent_events[-1]
    assert isinstance(response_event, events.ResponseCreateEvent)
    assert response_event.response.tool_choice == "none"
    prompt = response_event.response.instructions
    assert "The phone call has just connected. Greet the caller now:" in prompt
    assert prompt.endswith('"Hi Sam, this is Sarah from Acme."')
    assert service._llm_needs_conversation_setup is False
    service._create_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_tts_greeting_waits_for_session_updated_before_sending_prompt():
    service = _make_service()
    service._context = LLMContext()

    await service.process_frame(
        TTSSpeakFrame("Hello from Dograh.", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    assert service._handled_initial_context is True
    assert service._run_llm_when_api_session_ready is True
    assert service._pending_initial_greeting_text == "Hello from Dograh."

    service.send_client_event = AsyncMock()
    service.push_frame = AsyncMock()
    service.start_processing_metrics = AsyncMock()
    service.start_ttfb_metrics = AsyncMock()

    await service._handle_evt_session_updated(SimpleNamespace())

    sent_events = [call.args[0] for call in service.send_client_event.await_args_list]
    assert not any(
        isinstance(event, events.ConversationItemCreateEvent) for event in sent_events
    )
    assert isinstance(sent_events[0], events.SessionUpdateEvent)
    response_event = sent_events[-1]
    assert isinstance(response_event, events.ResponseCreateEvent)
    assert response_event.response.tool_choice == "none"
    prompt = response_event.response.instructions
    assert prompt.endswith('"Hello from Dograh."')
    assert service._run_llm_when_api_session_ready is False
    assert service._pending_initial_greeting_text is None
    assert service._llm_needs_conversation_setup is False
    service._create_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_function_call_executes_immediately_when_bot_is_not_speaking():
    service = _make_service()
    service._context = LLMContext()
    service.run_function_calls = AsyncMock()
    service._pending_function_calls["call-1"] = SimpleNamespace(name="customer_support")

    await service._handle_evt_function_call_arguments_done(
        SimpleNamespace(call_id="call-1", arguments='{"department":"sales"}')
    )

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_function_calls == []


@pytest.mark.asyncio
async def test_function_call_is_deferred_until_bot_stops_speaking():
    service = _make_service()
    service._context = LLMContext()
    service.run_function_calls = AsyncMock()
    service._bot_is_speaking = True
    service._pending_function_calls["call-1"] = SimpleNamespace(name="customer_support")

    await service._handle_evt_function_call_arguments_done(
        SimpleNamespace(call_id="call-1", arguments='{"department":"sales"}')
    )

    service.run_function_calls.assert_not_awaited()
    assert len(service._deferred_function_calls) == 1

    await service._run_pending_function_calls()

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_function_calls == []
