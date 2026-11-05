"""Replay Grok transcription snapshots through parsing and frame emission."""

import json
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.xai.realtime.llm import GrokRealtimeLLMService

from api.services.pipecat.realtime.grok_realtime import DograhGrokRealtimeLLMService


@pytest.fixture(params=[GrokRealtimeLLMService, DograhGrokRealtimeLLMService])
def service(request):
    service = request.param(api_key="test-key")
    service.push_frame = AsyncMock()
    service._call_event_handler = AsyncMock()
    return service


async def replay(service, *snapshots):
    async def messages():
        for index, snapshot in enumerate(snapshots):
            yield json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "event_id": f"event-{index}",
                    "item_id": "user-item-1",
                    "content_index": 0,
                    **snapshot,
                }
            )

    service._websocket = messages()
    try:
        await service._receive_task_handler()
    finally:
        service._websocket = None

    return [
        call.args[0]
        for call in service.push_frame.await_args_list
        if len(call.args) > 1 and call.args[1] == FrameDirection.UPSTREAM
    ]


@pytest.mark.asyncio
async def test_provisional_completed_events_do_not_repeat_the_user_transcript(service):
    # This sequence was captured from one spoken "Can you hear me?" on Grok.
    frames = await replay(
        service,
        {
            "type": "conversation.item.input_audio_transcription.updated",
            "transcript": "Can you hear",
        },
        {"status": "in_progress", "transcript": "Can you hear"},
        {"status": "in_progress", "transcript": "Can you hear me?"},
        {"status": "completed", "transcript": "Can you hear me?"},
    )

    assert [type(frame) for frame in frames] == [
        InterimTranscriptionFrame,
        InterimTranscriptionFrame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
    ]
    assert [frame.result.status for frame in frames[1:]] == [
        "in_progress",
        "in_progress",
        "completed",
    ]
    service._call_event_handler.assert_awaited_once_with(
        "on_conversation_item_updated", "user-item-1", None
    )

    user = LLMUserAggregator(LLMContext())
    for frame in frames:
        if isinstance(frame, TranscriptionFrame):
            await user._handle_transcription(frame)
    assert user.aggregation_string() == "Can you hear me?"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_fields", [{}, {"status": None}])
async def test_legacy_completed_event_without_status_is_final(service, status_fields):
    frames = await replay(service, {"transcript": "Hello there", **status_fields})

    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "Hello there"


@pytest.mark.asyncio
async def test_same_phrase_from_distinct_audio_items_is_preserved(service):
    frames = await replay(
        service,
        {"item_id": "user-item-1", "status": "completed", "transcript": "Okay."},
        {"item_id": "user-item-2", "status": "completed", "transcript": "Okay."},
    )

    assert [frame.text for frame in frames] == ["Okay.", "Okay."]
    assert all(isinstance(frame, TranscriptionFrame) for frame in frames)
    assert [frame.result.item_id for frame in frames] == ["user-item-1", "user-item-2"]
