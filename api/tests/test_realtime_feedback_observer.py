from types import SimpleNamespace

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSTextFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver


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
