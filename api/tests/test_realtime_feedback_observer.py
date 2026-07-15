import re
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    TranscriptionFrame,
    TTSTextFrame,
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
from api.services.pipecat.transcript_log_coordinator import TranscriptLogCoordinator


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
    coordinator = TranscriptLogCoordinator(logs_buffer)
    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()

    register_turn_log_handlers(coordinator, user_aggregator, assistant_aggregator)

    assert "on_user_turn_message_added" in user_aggregator.handlers
    assert "on_user_turn_stopped" not in user_aggregator.handlers

    logs_buffer.set_current_node("node-a", "Node A")
    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="Hi there",
            timestamp="2026-01-01T00:00:00+00:00",
        ),
    )
    logs_buffer.set_current_node("node-b", "Node B")
    await coordinator.record_turn_ended(1, interrupted=False)

    events = logs_buffer.get_events()
    assert len(events) == 1
    assert events[0]["type"] == "rtf-user-transcription"
    assert events[0]["payload"] == {
        "text": "Hi there",
        "final": True,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    assert events[0]["turn"] == 1
    assert events[0]["node_id"] == "node-a"
    assert events[0]["node_name"] == "Node A"


@pytest.mark.asyncio
async def test_coordinator_attaches_speaking_intervals_to_logged_transcript_events():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    coordinator = TranscriptLogCoordinator(logs_buffer)

    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()
    register_turn_log_handlers(coordinator, user_aggregator, assistant_aggregator)

    await coordinator.record_turn_started(1)
    await coordinator.record_user_started_speaking(1)
    await coordinator.record_user_stopped_speaking(1)
    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="January fifth",
            timestamp="aggregator-user-start",
        ),
    )

    await coordinator.record_bot_started_speaking(1)
    await assistant_aggregator.handlers["on_assistant_turn_stopped"](
        assistant_aggregator,
        SimpleNamespace(
            content="Thank you",
            timestamp="aggregator-bot-start",
        ),
    )
    await assistant_aggregator.handlers["on_assistant_turn_stopped"](
        assistant_aggregator,
        SimpleNamespace(
            content="You're welcome",
            timestamp="second-aggregator-bot-start",
        ),
    )
    await coordinator.record_bot_stopped_speaking(1)
    await coordinator.record_turn_ended(1, interrupted=False)

    user_event, bot_event = [
        event
        for event in logs_buffer.get_events()
        if event["type"] in {"rtf-user-transcription", "rtf-bot-text"}
    ]

    assert user_event["turn"] == 1
    assert bot_event["turn"] == 1
    assert user_event["payload"]["timestamp"] != "aggregator-user-start"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
        user_event["payload"]["timestamp"],
    )
    assert user_event["payload"]["end_timestamp"]
    assert bot_event["payload"]["timestamp"] != "aggregator-bot-start"
    assert bot_event["payload"]["text"] == "Thank you\nYou're welcome"
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$",
        bot_event["payload"]["timestamp"],
    )
    assert bot_event["payload"]["end_timestamp"]


@pytest.mark.asyncio
async def test_user_speaking_frames_define_full_multi_segment_turn_envelope():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    coordinator = TranscriptLogCoordinator(logs_buffer)
    user_aggregator = _FakeAggregator()
    assistant_aggregator = _FakeAggregator()
    register_turn_log_handlers(coordinator, user_aggregator, assistant_aggregator)

    await coordinator.record_turn_started(2)
    await coordinator.record_user_started_speaking(2, "2026-07-14T09:55:22.132+00:00")
    await coordinator.record_user_stopped_speaking(2, "2026-07-14T09:55:27.713+00:00")
    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="Yeah, yeah. I'm just like,",
            timestamp="2026-07-14T09:55:22.132+00:00",
        ),
    )

    # The user resumes before the bot speaks, so this remains canonical Turn 2.
    await coordinator.record_user_started_speaking(2, "2026-07-14T09:55:28.393+00:00")
    await coordinator.record_user_stopped_speaking(2, "2026-07-14T09:55:29.994+00:00")
    await user_aggregator.handlers["on_user_turn_message_added"](
        user_aggregator,
        SimpleNamespace(
            content="what to get",
            timestamp="2026-07-14T09:55:28.393+00:00",
        ),
    )
    await coordinator.record_turn_ended(2, interrupted=False)

    user_event = logs_buffer.get_events()[0]
    assert user_event["turn"] == 2
    assert user_event["payload"] == {
        "text": "Yeah, yeah. I'm just like,\nwhat to get",
        "final": True,
        "timestamp": "2026-07-14T09:55:22.132+00:00",
        "end_timestamp": "2026-07-14T09:55:29.994+00:00",
    }


@pytest.mark.asyncio
async def test_appended_events_are_not_mutated_by_later_turn_activity():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    first = {"type": "rtf-bot-text", "payload": {"text": "First"}}

    await logs_buffer.append(first, turn=1)
    first["payload"]["text"] = "Mutated outside the buffer"
    logs_buffer.set_current_turn(2)
    await logs_buffer.append({"type": "rtf-bot-text", "payload": {"text": "Second"}})

    assert logs_buffer.get_events()[0]["payload"] == {"text": "First"}


@pytest.mark.asyncio
async def test_stored_events_are_sorted_by_event_timestamp_not_payload_timestamp():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)

    await logs_buffer.append(
        {
            "type": "rtf-bot-text",
            "payload": {"text": "Speech started first", "timestamp": "payload-1"},
        },
        timestamp="2026-01-01T00:00:02.000+00:00",
        turn=1,
    )
    await logs_buffer.append(
        {
            "type": "rtf-node-transition",
            "payload": {"timestamp": "payload-2"},
        },
        timestamp="2026-01-01T00:00:01.000+00:00",
        turn=1,
    )

    assert [event["timestamp"] for event in logs_buffer.get_events()] == [
        "2026-01-01T00:00:01.000+00:00",
        "2026-01-01T00:00:02.000+00:00",
    ]


@pytest.mark.asyncio
async def test_completed_user_turn_does_not_reuse_speaking_frame_timestamps():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=123)
    coordinator = TranscriptLogCoordinator(logs_buffer)

    await coordinator.record_turn_started(1)
    await coordinator.record_user_started_speaking(1, "2026-01-01T00:00:01.000+00:00")
    await coordinator.record_user_stopped_speaking(1, "2026-01-01T00:00:02.000+00:00")
    await coordinator.record_user_transcript(text="First", timestamp=None)
    await coordinator.record_turn_ended(1, interrupted=False)

    await coordinator.record_turn_started(2)
    await coordinator.record_user_started_speaking(2, "2026-01-01T00:00:10.000+00:00")
    await coordinator.record_user_stopped_speaking(2, "2026-01-01T00:00:12.000+00:00")
    await coordinator.record_user_transcript(text="Second", timestamp=None)
    await coordinator.record_turn_ended(2, interrupted=False)

    second_event = logs_buffer.get_events()[-1]
    assert second_event["payload"]["timestamp"] == "2026-01-01T00:00:10.000+00:00"
    assert second_event["payload"]["end_timestamp"] == "2026-01-01T00:00:12.000+00:00"


@pytest.mark.asyncio
async def test_interrupted_bot_transcript_keeps_the_interrupted_turn_interval():
    logs_buffer = InMemoryLogsBuffer(workflow_run_id=2122)
    coordinator = TranscriptLogCoordinator(logs_buffer)

    await coordinator.record_turn_started(2)
    await coordinator.record_bot_started_speaking(2, "2026-07-14T13:33:02.254+00:00")

    # The user interrupts: logical Turn 2 ends and Turn 3 starts before the
    # output transport reports that Turn 2's audio has physically stopped.
    await coordinator.record_turn_ended(2, interrupted=True)
    await coordinator.record_turn_started(3)
    await coordinator.record_bot_stopped_speaking(2, "2026-07-14T13:33:03.817+00:00")
    await coordinator.record_assistant_transcript(
        text="A minivan, too easy,",
        timestamp="2026-07-14T13:33:06.654+00:00",
        event_timestamp="2026-07-14T13:33:03.819+00:00",
    )

    [event] = logs_buffer.get_events()
    assert event["type"] == "rtf-bot-text"
    assert event["timestamp"] == "2026-07-14T13:33:03.819+00:00"
    assert event["turn"] == 2
    assert event["payload"] == {
        "text": "A minivan, too easy,",
        "timestamp": "2026-07-14T13:33:02.254+00:00",
        "end_timestamp": "2026-07-14T13:33:03.817+00:00",
    }

    await coordinator.record_bot_started_speaking(3, "2026-07-14T13:33:06.654+00:00")
    assert event["payload"]["timestamp"] == "2026-07-14T13:33:02.254+00:00"
