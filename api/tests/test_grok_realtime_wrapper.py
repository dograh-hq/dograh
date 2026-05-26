from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.xai.realtime import events

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import GrokRealtimeLLMConfiguration
from api.services.pipecat.realtime.grok_realtime import (
    DograhGrokRealtimeLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service


def _make_service() -> DograhGrokRealtimeLLMService:
    service = DograhGrokRealtimeLLMService(api_key="test-key")
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
async def test_tts_greeting_uses_initial_context_handler():
    service = _make_service()
    service._context = LLMContext()
    service._handle_context = AsyncMock()

    await service.process_frame(
        TTSSpeakFrame("hello", append_to_context=True),
        FrameDirection.DOWNSTREAM,
    )

    service._handle_context.assert_awaited_once_with(service._context)


@pytest.mark.asyncio
async def test_messages_append_frame_sends_conversation_item():
    service = _make_service()
    service._api_session_ready = True
    service.send_client_event = AsyncMock()
    service._send_manual_response_create = AsyncMock()

    await service._handle_messages_append(
        LLMMessagesAppendFrame(
            [{"role": "user", "content": "Are you still there?"}],
            run_llm=True,
        )
    )

    service.send_client_event.assert_awaited_once()
    event = service.send_client_event.await_args.args[0]
    assert isinstance(event, events.ConversationItemCreateEvent)
    assert event.item.role == "user"
    assert event.item.type == "message"
    assert event.item.content == [
        events.ItemContent(type="input_text", text="Are you still there?")
    ]
    service._send_manual_response_create.assert_awaited_once()


@pytest.mark.asyncio
async def test_function_call_is_deferred_until_bot_stops_speaking():
    service = _make_service()
    service._context = LLMContext()
    service.run_function_calls = AsyncMock()
    service._bot_is_speaking = True
    service._pending_function_calls["call-1"] = SimpleNamespace(name="customer_support")

    await service._handle_evt_function_call_arguments_done(
        SimpleNamespace(
            call_id="call-1",
            name="customer_support",
            arguments='{"department":"sales"}',
        )
    )

    service.run_function_calls.assert_not_awaited()
    assert len(service._deferred_function_calls) == 1

    await service._run_pending_function_calls()

    service.run_function_calls.assert_awaited_once()
    assert service._deferred_function_calls == []


@pytest.mark.asyncio
async def test_completed_input_transcription_is_broadcast_as_finalized():
    service = _make_service()
    service._call_event_handler = AsyncMock()
    service.broadcast_frame = AsyncMock()

    evt = SimpleNamespace(item_id="item-1", transcript="Hello there")

    await service._handle_evt_input_audio_transcription_completed(evt)

    service._call_event_handler.assert_awaited_once_with(
        "on_conversation_item_updated", "item-1", None
    )
    service.broadcast_frame.assert_awaited_once()
    assert service.broadcast_frame.await_args.args[0].__name__ == "TranscriptionFrame"
    assert service.broadcast_frame.await_args.kwargs["text"] == "Hello there"
    assert service.broadcast_frame.await_args.kwargs["finalized"] is True


def test_factory_creates_dograh_grok_realtime_service():
    user_config = UserConfiguration(
        is_realtime=True,
        realtime=GrokRealtimeLLMConfiguration(
            provider="grok_realtime",
            api_key="xai-key",
            model="grok-voice-think-fast-1.0",
            voice="Sal",
        ),
    )

    service = create_realtime_llm_service(
        user_config,
        audio_config=SimpleNamespace(),
    )

    assert isinstance(service, DograhGrokRealtimeLLMService)
