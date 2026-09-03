"""Tests for leaving a configured message at an answering machine.

Covers the three pieces added around the native VoicemailDetector:
- ``VoicemailMessageConfig`` parsing of ``voicemail_detection.leave_message``;
- ``UserSpeechMonitor`` deciding when the far end has gone quiet;
- ``handle_voicemail_detected`` ordering: wait → speak → wait for playback →
  hang up, and every fallback ending as plain ``voicemail_detected`` so a
  message that was not actually delivered is never reported as left.
"""

import asyncio
from typing import Any

import pytest
from pipecat.frames.frames import (
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat.voicemail_message import (
    UserSpeechMonitor,
    VoicemailMessageConfig,
    handle_voicemail_detected,
)
from api.services.workflow.end_reasons import VOICEMAIL_MESSAGE_LEFT
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_callbacks import UserIdleHandler

DETECTED = EndTaskReason.VOICEMAIL_DETECTED.value


class FakeTask:
    def __init__(self):
        self.frames: list[Any] = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakeEngine:
    """Just the surface handle_voicemail_detected touches, with call recording."""

    def __init__(self, *, playback: bool = True, started: bool = True):
        self.task = FakeTask()
        self.ends: list[tuple[str, bool]] = []
        self.events: list[str] = []
        self.marked = False
        self._playback = playback
        self.speech_playback_started = started

    def mark_voicemail_detected(self):
        self.marked = True

    def render_call_text(self, text: str) -> str:
        return text.replace("{{first_name | there}}", "Dana")

    def arm_speech_playback(self):
        self.events.append("arm")

    async def wait_for_speech_playback(self, *, start_timeout, playback_timeout):
        self.events.append(f"wait_playback:{playback_timeout}")
        return self._playback

    async def end_call_with_reason(self, reason, abort_immediately=False):
        self.events.append(f"end:{reason}")
        self.ends.append((reason, abort_immediately))


class FakeMonitor:
    def __init__(self, quiet: bool = True):
        self.quiet = quiet
        self.calls: list[tuple[float, float]] = []

    async def wait_for_silence(self, quiet_secs, *, timeout):
        self.calls.append((quiet_secs, timeout))
        return self.quiet


ACTIVE = VoicemailMessageConfig(enabled=True, text="Hi {{first_name | there}}, call us back.")


class TestVoicemailMessageConfig:
    def test_absent_means_disabled(self):
        assert VoicemailMessageConfig.from_voicemail_config({"enabled": True}).active is False
        assert VoicemailMessageConfig.from_voicemail_config(None).active is False

    def test_enabled_without_text_is_not_active(self):
        cfg = VoicemailMessageConfig.from_voicemail_config(
            {"leave_message": {"enabled": True, "text": "   "}}
        )
        assert cfg.enabled is True and cfg.text == "" and cfg.active is False

    def test_parses_text_and_timings_with_defaults(self):
        cfg = VoicemailMessageConfig.from_voicemail_config(
            {
                "leave_message": {
                    "enabled": True,
                    "text": "  Hello there  ",
                    "greeting_end_silence_secs": "1.5",
                    "max_greeting_wait_secs": -4,
                }
            }
        )
        assert cfg.active is True
        assert cfg.text == "Hello there"
        assert cfg.greeting_end_silence_secs == 1.5
        # invalid values fall back to the defaults rather than breaking the run
        assert cfg.max_greeting_wait_secs == 30.0
        assert cfg.playback_timeout_secs == 60.0

    def test_non_dict_config_is_ignored(self):
        assert VoicemailMessageConfig.from_voicemail_config({"leave_message": "yes"}).active is False


class TestHandleVoicemailDetected:
    @pytest.mark.asyncio
    async def test_disabled_ends_the_call_immediately_as_before(self):
        engine = FakeEngine()
        reason = await handle_voicemail_detected(
            engine, None, VoicemailMessageConfig(), workflow_run_id=1
        )
        assert reason == DETECTED
        assert engine.ends == [(DETECTED, True)]
        assert engine.task.frames == []
        assert engine.marked is True

    @pytest.mark.asyncio
    async def test_waits_speaks_then_hangs_up_as_message_left(self):
        engine = FakeEngine()
        monitor = FakeMonitor(quiet=True)
        reason = await handle_voicemail_detected(engine, monitor, ACTIVE, workflow_run_id=7)

        assert reason == VOICEMAIL_MESSAGE_LEFT
        assert monitor.calls == [(2.0, 30.0)]
        # arm BEFORE queueing, wait for playback AFTER, and only then end
        assert engine.events == ["arm", "wait_playback:60.0", f"end:{VOICEMAIL_MESSAGE_LEFT}"]
        [frame] = engine.task.frames
        assert isinstance(frame, TTSSpeakFrame)
        assert frame.text == "Hi Dana, call us back."
        assert frame.append_to_context is False
        # graceful EndFrame so the audio path is torn down after playback
        assert engine.ends == [(VOICEMAIL_MESSAGE_LEFT, False)]

    @pytest.mark.asyncio
    async def test_far_end_never_quiet_hangs_up_without_speaking(self):
        engine = FakeEngine()
        reason = await handle_voicemail_detected(
            engine, FakeMonitor(quiet=False), ACTIVE, workflow_run_id=1
        )
        assert reason == DETECTED
        assert engine.task.frames == []
        assert engine.ends == [(DETECTED, True)]

    @pytest.mark.asyncio
    async def test_playback_failure_is_not_reported_as_message_left(self):
        engine = FakeEngine(playback=False, started=False)
        reason = await handle_voicemail_detected(
            engine, FakeMonitor(quiet=True), ACTIVE, workflow_run_id=1
        )
        assert reason == DETECTED
        assert len(engine.task.frames) == 1  # it did try
        assert engine.ends == [(DETECTED, False)]


class TestUserSpeechMonitor:
    @pytest.mark.asyncio
    async def test_passes_frames_through_and_tracks_boundaries(self):
        monitor = UserSpeechMonitor()
        frames = [
            VADUserStartedSpeakingFrame(),
            TranscriptionFrame(text="you have reached", user_id="u", timestamp="t"),
            VADUserStoppedSpeakingFrame(),
        ]
        # System frames (the VAD boundaries) overtake queued data frames, so
        # arrival order is not asserted here — passthrough is pinned by the
        # single-frame tests below; this one pins the tracked state.
        down, _ = await run_test(monitor, frames_to_send=frames)
        assert {type(f) for f in down} >= {
            VADUserStartedSpeakingFrame,
            TranscriptionFrame,
            VADUserStoppedSpeakingFrame,
        }
        assert monitor.speaking is False
        assert await monitor.wait_for_silence(0.05, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_aggregator_boundaries_count_in_either_direction(self):
        monitor = UserSpeechMonitor()
        await run_test(
            monitor,
            frames_to_send=[UserStartedSpeakingFrame()],
            frames_to_send_direction=FrameDirection.UPSTREAM,
            expected_up_frames=[UserStartedSpeakingFrame],
        )
        assert monitor.speaking is True
        # mid-utterance is never "quiet", however long the window
        assert await monitor.wait_for_silence(0.0, timeout=0.3, poll_secs=0.05) is False

        await run_test(
            monitor,
            frames_to_send=[UserStoppedSpeakingFrame()],
            expected_down_frames=[UserStoppedSpeakingFrame],
        )
        assert monitor.speaking is False
        assert await monitor.wait_for_silence(0.05, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_transcript_activity_resets_the_quiet_window(self):
        monitor = UserSpeechMonitor()
        await run_test(
            monitor,
            frames_to_send=[UserStoppedSpeakingFrame()],
            expected_down_frames=[UserStoppedSpeakingFrame],
        )
        # a late transcript proves speech was still arriving
        await run_test(
            monitor,
            frames_to_send=[TranscriptionFrame(text="beep", user_id="u", timestamp="t")],
            expected_down_frames=[TranscriptionFrame],
        )
        assert await monitor.wait_for_silence(5.0, timeout=0.2, poll_secs=0.05) is False


class FakeAggregator:
    def __init__(self):
        self.frames: list[Any] = []

    async def push_frame(self, frame):
        self.frames.append(frame)


def make_engine(**call_context_vars) -> PipecatEngine:
    return PipecatEngine(workflow=None, call_context_vars=call_context_vars, workflow_run_id=1)


class TestEngineSurface:
    def test_render_call_text_uses_call_context(self):
        engine = make_engine(first_name="Dana")
        assert engine.render_call_text("Hi {{first_name | there}}") == "Hi Dana"
        assert make_engine().render_call_text("Hi {{first_name | there}}") == "Hi there"

    @pytest.mark.asyncio
    async def test_idle_escalation_is_suppressed_after_voicemail_detection(self):
        engine = make_engine()
        handler = UserIdleHandler(engine)
        aggregator = FakeAggregator()

        await handler.handle_idle(aggregator)
        assert len(aggregator.frames) == 1
        assert isinstance(aggregator.frames[0], LLMMessagesAppendFrame)

        engine.mark_voicemail_detected()
        assert engine.voicemail_detected is True
        aggregator.frames.clear()
        # second escalation would normally hang the call up
        await handler.handle_idle(aggregator)
        assert aggregator.frames == []
        assert engine._call_disposed is False
