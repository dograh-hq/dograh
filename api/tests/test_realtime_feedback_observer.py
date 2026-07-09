import re
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.realtime_feedback_observer import (
    RealtimeFeedbackObserver,
    register_turn_log_handlers,
)


class _FakeAggregator:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, event_name):
        def decorator(handler):
            self.handlers[event_name] = handler
            return handler

        return decorator


def _frame_pushed(frame, direction, *, source=None):
    return FramePushed(
        source=source or SimpleNamespace(),
        destination=SimpleNamespace(),
        frame=frame,
        direction=direction,
        timestamp=0,
    )


@pytest.mark.asyncio
async def test_observer_streams_upstream_only_transcription_frames():
    messages = []

    async def ws_sender(message):
        messages.append(message)

    observer = RealtimeFeedbackObserver(ws_sender=ws_sender)
    frame = TranscriptionFrame(
        "Hi there",
        user_id="user-1",
        timestamp="2026-01-01T00:00:00+00:00",
    )

    await observer.on_push_frame(_frame_pushed(frame, FrameDirection.UPSTREAM))

    assert messages == [
        {
            "type": "rtf-user-transcription",
            "payload": {
                "text": "Hi there",
                "final": True,
                "timestamp": "2026-01-01T00:00:00+00:00",
                "user_id": "user-1",
            },
        }
    ]


@pytest.mark.asyncio
async def test_observer_ignores_upstream_broadcast_transcription_sibling():
    messages = []

    async def ws_sender(message):
        messages.append(message)

    observer = RealtimeFeedbackObserver(ws_sender=ws_sender)
    frame = TranscriptionFrame(
        "Hi there",
        user_id="user-1",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    frame.broadcast_sibling_id = 1234

    await observer.on_push_frame(_frame_pushed(frame, FrameDirection.UPSTREAM))

    assert messages == []


@pytest.mark.asyncio
async def test_observer_waits_for_tts_text_from_output_transport():
    messages = []

    async def ws_sender(message):
        messages.append(message)

    observer = RealtimeFeedbackObserver(ws_sender=ws_sender)
    frame = TTSTextFrame("Hello", aggregated_by="word")
    frame.pts = 123

    await observer.on_push_frame(_frame_pushed(frame, FrameDirection.DOWNSTREAM))
    assert messages == []

    output_transport = BaseOutputTransport(TransportParams())
    await observer.on_push_frame(
        _frame_pushed(
            frame,
            FrameDirection.DOWNSTREAM,
            source=output_transport,
        )
    )

    assert messages == [
        {
            "type": "rtf-bot-text",
            "payload": {"text": "Hello"},
        }
    ]


@pytest.mark.asyncio
async def test_turn_log_handlers_persist_user_message_added_events():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()

    register_turn_log_handlers(logs_buffer, user_aggregator, assistant_aggregator)

    assert "on_user_turn_message_added" in user_aggregator.handlers
    assert "on_user_turn_stopped" not in user_aggregator.handlers

    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="Hi there",
            timestamp="2026-01-01T00:00:00+00:00",
        ),
    )

    events = logs_buffer.get_events()
    assert len(events) == 1
    assert events[0]["type"] == "rtf-user-transcription"
    assert events[0]["payload"] == {
        "text": "Hi there",
        "final": True,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    assert events[0]["turn"] == 1


@pytest.mark.asyncio
async def test_observer_attaches_backend_speaking_intervals_to_logged_transcript_events():
    async def ws_sender(_message):
        pass

    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    observer = RealtimeFeedbackObserver(ws_sender=ws_sender, logs_buffer=logs_buffer)

    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()
    register_turn_log_handlers(logs_buffer, user_aggregator, assistant_aggregator)

    await observer.on_push_frame(
        _frame_pushed(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    )
    await observer.on_push_frame(
        _frame_pushed(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    )
    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="January fifth",
            timestamp="aggregator-user-start",
        ),
    )

    await observer.on_push_frame(
        _frame_pushed(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    )
    await assistant_aggregator.handlers["on_assistant_turn_stopped"](
        assistant_aggregator,
        SimpleNamespace(
            content="Thank you",
            timestamp="aggregator-bot-start",
        ),
    )
    await observer.on_push_frame(
        _frame_pushed(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    )

    user_event, bot_event = [
        event
        for event in logs_buffer.get_events()
        if event["type"] in {"rtf-user-transcription", "rtf-bot-text"}
    ]

    assert user_event["payload"]["timestamp"] != "aggregator-user-start"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
        user_event["payload"]["timestamp"],
    )
    assert user_event["payload"]["end_timestamp"]
    assert bot_event["payload"]["timestamp"] != "aggregator-bot-start"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
        bot_event["payload"]["timestamp"],
    )
    assert bot_event["payload"]["end_timestamp"]


@pytest.mark.asyncio
async def test_observer_uses_vad_user_speech_times_over_turn_closure_times():
    async def ws_sender(_message):
        pass

    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    observer = RealtimeFeedbackObserver(ws_sender=ws_sender, logs_buffer=logs_buffer)
    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()
    register_turn_log_handlers(logs_buffer, user_aggregator, assistant_aggregator)

    await observer.on_push_frame(
        _frame_pushed(
            VADUserStartedSpeakingFrame(start_secs=0.2, timestamp=1000.2),
            FrameDirection.DOWNSTREAM,
        )
    )
    await observer.on_push_frame(
        _frame_pushed(
            VADUserStoppedSpeakingFrame(stop_secs=0.5, timestamp=1002.5),
            FrameDirection.DOWNSTREAM,
        )
    )
    await observer.on_push_frame(
        _frame_pushed(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    )

    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(content="Hello", timestamp="aggregator-user-start"),
    )

    user_event = logs_buffer.get_events()[0]
    assert user_event["payload"]["timestamp"] == "1970-01-01T00:16:40.000+00:00"
    assert user_event["payload"]["end_timestamp"] == "1970-01-01T00:16:42.000+00:00"


@pytest.mark.asyncio
async def test_completed_bot_speech_interval_is_not_reused_for_next_pre_playback_text():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)

    logs_buffer.mark_bot_started_speaking("2026-01-01T00:00:01.000+00:00")
    await logs_buffer.append({"type": "rtf-bot-text", "payload": {"text": "First"}})
    logs_buffer.mark_bot_stopped_speaking("2026-01-01T00:00:02.000+00:00")

    await logs_buffer.append({"type": "rtf-bot-text", "payload": {"text": "Second"}})
    second_event = logs_buffer.get_events()[-1]

    assert second_event["payload"] == {"text": "Second"}


@pytest.mark.asyncio
async def test_non_vad_user_turn_after_completed_vad_turn_gets_fresh_timestamps():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)

    logs_buffer.mark_user_started_speaking(
        "2026-01-01T00:00:01.000+00:00", from_vad=True
    )
    logs_buffer.mark_user_stopped_speaking(
        "2026-01-01T00:00:02.000+00:00", from_vad=True
    )
    await logs_buffer.append(
        {"type": "rtf-user-transcription", "payload": {"text": "First", "final": True}}
    )

    logs_buffer.mark_user_started_speaking("2026-01-01T00:00:10.000+00:00")
    logs_buffer.mark_user_stopped_speaking("2026-01-01T00:00:12.000+00:00")
    await logs_buffer.append(
        {"type": "rtf-user-transcription", "payload": {"text": "Second", "final": True}}
    )

    second_event = logs_buffer.get_events()[-1]
    assert second_event["payload"]["timestamp"] == "2026-01-01T00:00:10.000+00:00"
    assert second_event["payload"]["end_timestamp"] == "2026-01-01T00:00:12.000+00:00"
