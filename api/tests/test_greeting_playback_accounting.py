from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import SpeechBoundaryFrame, TTSAudioRawFrame, TTSTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.greeting import GreetingController
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver
from api.services.pipecat.recording_word_timings import (
    estimate_word_ends,
    recording_prefix,
)
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlaybackTracker


def audio(seconds, sample_rate=16000, channels=1):
    return TTSAudioRawFrame(
        b"\x01\x00" * round(seconds * sample_rate * channels), sample_rate, channels
    )


def test_estimated_word_boundaries_preserve_original_text():
    text = "Hi,   Ann!\nHello."
    assert estimate_word_ends(text, 6) == ((3, 1.2), (10, 3.0), (17, 6.0))
    assert recording_prefix(text, 6, 1.19) == ""
    assert recording_prefix(text, 6, 1.2) == "Hi,"
    assert recording_prefix(text, 6, 3) == "Hi,   Ann!"
    assert recording_prefix(text, 6, 20) == text


@pytest.mark.parametrize("text,duration", [("", 1), ("  ", 1), ("Hello", 0)])
def test_invalid_recording_has_no_estimated_words(text, duration):
    assert estimate_word_ends(text, duration) == ()


def test_recording_timing_cache_tracks_text_and_duration():
    first = estimate_word_ends("Hi there", 2)
    assert estimate_word_ends("Hi there", 2) is first
    assert estimate_word_ends("Hi there", 4) != first
    assert estimate_word_ends("Hello there", 2) != first


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", list(PlaybackOutcome))
async def test_no_output_never_commits_requested_text(outcome):
    tracker = SpeechPlaybackTracker()
    context = LLMContext()
    controller = GreetingController(
        tracker, lambda: context, is_screening=lambda: False
    )
    controller.log_speech = AsyncMock()
    speech = tracker.create(greeting=True)
    speech.text = "Hello there."
    speech.finish(outcome)
    await controller.flush()
    assert context.messages == []
    assert speech.delivered_text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        PlaybackOutcome.INTERRUPTED,
        PlaybackOutcome.CLOSED,
        PlaybackOutcome.FAILED,
        PlaybackOutcome.TIMED_OUT,
    ],
)
@pytest.mark.parametrize("source", ["audio", "text", "generated"])
async def test_partial_greeting_commits_the_same_text_once(source, outcome):
    tracker = SpeechPlaybackTracker()
    context = LLMContext(messages=[{"role": "assistant", "content": "Hi,"}])
    logs = InMemoryLogsBuffer(1)
    logs.set_current_turn(1)
    logs.set_current_node("start", "Start")
    feedback = RealtimeFeedbackObserver(AsyncMock(), logs)
    controller = GreetingController(
        tracker, lambda: context, is_screening=lambda: False
    )
    controller.log_speech = feedback.log_greeting
    controller.get_log_context = feedback.speech_log_context
    speech = tracker.create(greeting=True)
    speech.text = None if source == "generated" else "Hi, there."
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=True))
    if source == "audio":
        speech.recording_duration = 7
        speech.timing_source = "estimated"
        tracker.after_output(None, audio(2))
    else:
        first = TTSTextFrame("Hi,", aggregated_by="word")
        first.append_to_context = False
        first.pts = 1_000_000_000
        tracker._capture_greeting_text(None, first)
        tracker.after_output(None, audio(2))
        second = TTSTextFrame("there.", aggregated_by="word")
        second.pts = 3_000_000_000
        tracker._capture_greeting_text(None, second)
        assert not second.append_to_context
    logs.set_current_turn(2)
    logs.set_current_node("next", "Next")
    speech.finish(outcome)
    speech.finish(outcome)
    await controller.flush()
    assert context.messages == [
        {"role": "assistant", "content": "Hi,"},
        {"role": "assistant", "content": "Hi,"},
    ]
    events = logs.get_events()
    assert len(events) == 1
    assert events[0]["payload"]["text"] == speech.delivered_text == "Hi,"
    assert events[0]["payload"]["played_ms"] == 2000
    assert events[0]["payload"]["outcome"] == outcome.value
    assert events[0]["node_id"] == "start"
    assert events[0]["turn"] == 1
    assert events[0]["payload"]["timestamp"] == speech.first_output_at


@pytest.mark.asyncio
async def test_word_clock_cannot_advance_delivery_during_output_stall():
    tracker = SpeechPlaybackTracker()
    speech = tracker.create(greeting=True)
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=True))
    first = TTSTextFrame("Hello", aggregated_by="word")
    first.pts = 1_000_000_000
    tracker._capture_greeting_text(None, first)
    tracker.after_output(None, audio(0.1))
    second = TTSTextFrame("there", aggregated_by="word")
    second.pts = 2_000_000_000
    tracker._capture_greeting_text(None, second)
    speech.finish(PlaybackOutcome.INTERRUPTED)
    assert speech.delivered_text == ""


@pytest.mark.asyncio
async def test_tagged_words_arriving_before_output_start_are_not_lost():
    tracker = SpeechPlaybackTracker()
    speech = tracker.create(greeting=True)
    first = TTSTextFrame("Hello", aggregated_by="word")
    first.pts = 1_000_000_000
    first.metadata["dograh_greeting_id"] = speech.id
    tracker._capture_greeting_text(None, first)
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=True))
    tracker.after_output(None, audio(0.5))
    second = TTSTextFrame("there", aggregated_by="word")
    second.pts = 1_500_000_000
    tracker._capture_greeting_text(None, second)
    speech.finish(PlaybackOutcome.INTERRUPTED)
    assert speech.delivered_text == "Hello"
    assert not first.append_to_context


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["audio", "text", "generated"])
async def test_complete_greeting_commits_full_text(source):
    tracker = SpeechPlaybackTracker()
    speech = tracker.create(greeting=True)
    speech.text = None if source == "generated" else "Hi there."
    if source == "audio":
        speech.recording_duration = 1
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=True))
    tracker.after_output(None, audio(1, sample_rate=24000, channels=2))
    if source == "generated":
        tracker._capture_greeting_text(
            None, TTSTextFrame("Hi there.", aggregated_by="word")
        )
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=False))
    assert speech.outcome is PlaybackOutcome.PLAYED
    assert speech.delivered_text == "Hi there."


@pytest.mark.asyncio
async def test_output_gaps_do_not_commit_full_requested_text():
    tracker = SpeechPlaybackTracker()
    speech = tracker.create(greeting=True)
    speech.text = "Hello there."
    speech.queued_seconds = 2
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=True))
    tracker.after_output(None, audio(0.1))
    tracker.after_output(None, SpeechBoundaryFrame(speech.id, beginning=False))
    assert speech.delivered_text == ""
