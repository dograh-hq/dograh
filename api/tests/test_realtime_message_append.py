from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMMessagesAppendFrame
from pipecat.services.openai.realtime import events

from api.services.pipecat.realtime.openai_realtime import (
    DograhOpenAIRealtimeLLMService,
)
from api.services.workflow.pipecat_engine_callbacks import UserIdleHandler


@pytest.mark.asyncio
async def test_openai_realtime_messages_append_frame_sends_conversation_item():
    service = DograhOpenAIRealtimeLLMService(api_key="test")
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
async def test_user_idle_handler_uses_realtime_append_path():
    engine = SimpleNamespace(
        llm=SimpleNamespace(),
        end_call_with_reason=AsyncMock(),
    )
    aggregator = SimpleNamespace(push_frame=AsyncMock())
    handler = UserIdleHandler(engine)

    await handler.handle_idle(aggregator)

    aggregator.push_frame.assert_awaited_once()
    frame = aggregator.push_frame.await_args.args[0]
    assert isinstance(frame, LLMMessagesAppendFrame)
    assert frame.run_llm is True
    assert frame.messages == [
        {
            "role": "user",
            "content": "The user has been quiet. Politely and briefly ask if they're still there in the language that the user has been speaking so far.",
        }
    ]
