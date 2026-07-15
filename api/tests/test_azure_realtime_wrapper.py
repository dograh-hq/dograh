from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.openai.realtime import events

from api.services.pipecat.realtime.azure_realtime import (
    DograhAzureRealtimeLLMService,
)


def _make_service() -> DograhAzureRealtimeLLMService:
    service = DograhAzureRealtimeLLMService(
        api_key="test-key",
        base_url="wss://example.test/openai/realtime",
    )
    service._create_response = AsyncMock()
    service._process_completed_function_calls = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_tts_greeting_sends_exact_static_greeting_prompt():
    service = _make_service()
    service._context = LLMContext([{"role": "user", "content": "Existing context"}])
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
    assert isinstance(sent_events[0], events.ConversationItemCreateEvent)
    assert sent_events[0].item.role == "user"
    assert sent_events[0].item.content[0].text == "Existing context"
    assert isinstance(sent_events[1], events.SessionUpdateEvent)
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
    service._context = LLMContext([{"role": "user", "content": "Existing context"}])

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
    assert isinstance(sent_events[0], events.ConversationItemCreateEvent)
    assert sent_events[0].item.content[0].text == "Existing context"
    assert isinstance(sent_events[1], events.SessionUpdateEvent)
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
async def test_non_transition_function_call_runs_while_bot_is_speaking():
    service = _make_service()
    service._context = LLMContext()
    service.run_function_calls = AsyncMock()
    service._bot_is_speaking = True
    service._pending_function_calls["call-1"] = SimpleNamespace(name="lookup_order")

    await service._handle_evt_function_call_arguments_done(
        SimpleNamespace(call_id="call-1", arguments='{"order_id":"123"}')
    )

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_node_transition_function_calls == []


@pytest.mark.asyncio
async def test_node_transition_function_call_waits_until_bot_stops_speaking():
    service = _make_service()
    service._context = LLMContext()
    service.run_function_calls = AsyncMock()
    service._bot_is_speaking = True
    service.register_function(
        "customer_support",
        AsyncMock(),
        is_node_transition=True,
    )
    service._pending_function_calls["call-1"] = SimpleNamespace(name="customer_support")

    await service._handle_evt_function_call_arguments_done(
        SimpleNamespace(call_id="call-1", arguments='{"department":"sales"}')
    )

    service.run_function_calls.assert_not_awaited()
    assert len(service._deferred_node_transition_function_calls) == 1

    await service._run_pending_node_transition_function_calls()

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_node_transition_function_calls == []
