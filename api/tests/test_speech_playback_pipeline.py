"""Playback contracts exercised through real aggregation, TTS and output queues."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    ProposedUserStartedSpeakingFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.tests.mock_tts_service import MockTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import CallbackUserMuteStrategy
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from api.services.pipecat.speech_playback import PlaybackOutcome
from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine


class ControlledTTS(MockTTSService):
    def __init__(self, **kwargs):
        super().__init__(mock_audio_duration_ms=80, frame_delay=0, **kwargs)
        self.requested = asyncio.Queue()
        self.gates = {}

    async def run_tts(self, text, context_id):
        await self.requested.put(text)
        if text in self.gates:
            await self.gates[text].wait()
        if text == "empty":
            return
        async for frame in super().run_tts(text, context_id):
            yield frame


class ControlledOutput(MockOutputTransport):
    def __init__(self):
        super().__init__(
            params=TransportParams(
                audio_out_enabled=True,
                audio_out_sample_rate=16000,
                audio_out_end_silence_secs=0,
            )
        )
        self.writing = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def write_audio_frame(self, frame):
        self.writing.set()
        await self.release.wait()
        return await super().write_audio_frame(frame)


class HeldResponseBoundaries(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.hold = False
        self.frames = asyncio.Queue()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if self.hold and isinstance(
            frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame)
        ):
            await self.frames.put(frame)
        else:
            await self.push_frame(frame, direction)


class PlaybackHarness:
    def __init__(self, *, hold_boundaries=False, timestamped_tts=False):
        self.engine = PipecatEngine(workflow=None, call_context_vars={})
        # A timestamped TTS releases a response's end when its audio finishes.
        self.tts = ControlledTTS(push_text_frames=not timestamped_tts)
        self.output = ControlledOutput()
        self.boundaries = HeldResponseBoundaries()
        self.user = LLMUserAggregator(
            LLMContext(),
            params=LLMUserAggregatorParams(
                user_mute_strategies=[
                    CallbackUserMuteStrategy(self.engine.should_mute_user)
                ],
                user_turn_strategies=UserTurnStrategies(
                    start=[ExternalUserTurnStartStrategy()],
                    stop=[ExternalUserTurnStopStrategy()],
                ),
            ),
        )
        processors = [self.user, self.tts]
        if hold_boundaries:
            self.boundaries.hold = True
            processors.append(self.boundaries)
        processors.append(self.output)
        self.worker = PipelineWorker(Pipeline(processors), enable_rtvi=False)
        self.engine.call_worker = self.worker
        # Configured speech enters the generation stage, as in a child worker.
        self.engine.active_agent.worker = SimpleNamespace(
            queue_frame=self.tts.queue_frame
        )
        self.engine.set_transport_output(self.output)

    async def queue(self, source="text", text="Hello.", *, mute=True, timeout=1):
        return await self.engine.queue_speech(
            **({"text": text} if source == "text" else {"audio": b"\x01\x00" * 1280}),
            mute_user=mute,
            timeout=timeout,
        )

    async def muted(self):
        return await self.engine.should_mute_user(Frame())

    async def emit_boundary(self, frame):
        await self.tts.push_frame(frame)
        return await asyncio.wait_for(self.boundaries.frames.get(), 1)


@pytest.fixture
async def playback(request):
    harness = PlaybackHarness(**getattr(request, "param", {}))
    ready = asyncio.Event()

    @harness.worker.event_handler("on_pipeline_started")
    async def started(*_):
        ready.set()

    runner = asyncio.create_task(run_pipeline_worker(harness.worker))
    try:
        await asyncio.wait_for(ready.wait(), 3)
        yield harness
    finally:
        harness.output.release.set()
        for gate in harness.tts.gates.values():
            gate.set()
        if not runner.done():
            await harness.worker.queue_frame(EndFrame())
        await asyncio.wait_for(runner, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["text", "recording"])
async def test_wait_finishes_only_after_output_writes(playback, source):
    playback.output.release.clear()
    speech = await playback.queue(source)
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    waiter = asyncio.create_task(speech.wait())
    try:
        await asyncio.sleep(0.01)
        assert not waiter.done()
        assert await playback.muted()
        playback.output.release.set()
        assert await asyncio.wait_for(waiter, 1)
        assert not await playback.muted()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_fire_and_forget_releases_mute_after_playback(playback):
    await playback.queue()
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    async with asyncio.timeout(1):
        while await playback.muted():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_interruption_before_audio_releases_wait_and_mute(playback):
    playback.tts.gates["Hello."] = asyncio.Event()
    speech = await playback.queue(timeout=10)
    await asyncio.wait_for(playback.tts.requested.get(), 1)
    # The first speech proposal also activates muting, but is admitted by the
    # aggregator's existing previous-frame policy and broadcasts an interruption.
    await playback.worker.queue_frame(ProposedUserStartedSpeakingFrame())
    assert not await asyncio.wait_for(speech.wait(), 0.5)
    assert not playback.output.writing.is_set()
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_interruption_during_audio_is_not_success(playback):
    playback.output.release.clear()
    speech = await playback.queue(mute=False)
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    await playback.worker.queue_frame(ProposedUserStartedSpeakingFrame())
    assert not await asyncio.wait_for(speech.wait(), 0.5)


@pytest.mark.asyncio
async def test_suppressed_interruption_does_not_cancel_playback(playback):
    playback.output.release.clear()
    speech = await playback.queue()
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    # Wait for the start notification to establish the aggregator's mute.
    async with asyncio.timeout(1):
        while not playback.user._user_is_muted:
            await asyncio.sleep(0)
    await playback.worker.queue_frame(InterruptionFrame())
    await asyncio.sleep(0.01)
    playback.output.release.set()
    assert await asyncio.wait_for(speech.wait(), 1)


@pytest.mark.asyncio
async def test_timeout_without_waiter_releases_mute(playback):
    playback.tts.gates["Hello."] = asyncio.Event()
    await playback.queue(timeout=0.05)
    await asyncio.sleep(0.1)
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_empty_audio_finishes_without_success_or_sticky_mute(playback):
    speech = await playback.queue(text="empty", timeout=0.1)
    assert not await asyncio.wait_for(speech.wait(), 1)
    # This service emits no terminal TTS frame either; its serializer holds
    # the marker, so the operation's deadline must release the mute.
    assert speech.outcome is PlaybackOutcome.TIMED_OUT
    assert not await playback.muted()


@pytest.mark.asyncio
@pytest.mark.parametrize("playback", [{"timestamped_tts": True}], indirect=True)
async def test_textless_response_ends_without_waiting_for_its_deadline(playback):
    # An empty generation gives a timestamped TTS no audio to release its end
    # marker; the expected response must still resolve, not hold the caller
    # muted until the deadline.
    speech = playback.engine.speech_playback.expect_response(
        source=playback.user, mute_user=True, timeout=10
    )
    await playback.user.push_frame(LLMFullResponseStartFrame())
    await playback.user.push_frame(LLMFullResponseEndFrame())
    assert not await asyncio.wait_for(speech.wait(), 1)
    assert speech.outcome is PlaybackOutcome.FAILED
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_first_message_stop_does_not_unmute_pending_message(playback):
    playback.output.release.clear()
    first = await playback.queue(text="First.")
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    playback.tts.gates["Second."] = asyncio.Event()
    second = await playback.queue(text="Second.")
    playback.output.release.set()
    assert await asyncio.wait_for(first.wait(), 0.5)
    assert await playback.muted()
    playback.tts.gates["Second."].set()
    assert await asyncio.wait_for(second.wait(), 1)
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_recording_can_finish_while_text_is_still_generating(playback):
    playback.tts.gates["Hello."] = asyncio.Event()
    text = await playback.queue()
    await asyncio.wait_for(playback.tts.requested.get(), 1)
    recording = await playback.queue("recording")
    assert await asyncio.wait_for(recording.wait(), 1)
    assert not text.done
    assert await playback.muted()
    playback.tts.gates["Hello."].set()
    assert await asyncio.wait_for(text.wait(), 1)
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_playback(playback):
    playback.output.release.clear()
    speech = await playback.queue()
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    waiter = asyncio.create_task(speech.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not speech.done
    assert await playback.muted()
    playback.output.release.set()
    assert await asyncio.wait_for(speech.wait(), 1)


@pytest.mark.asyncio
async def test_expired_operation_ignores_late_completion(playback):
    playback.tts.gates["Hello."] = asyncio.Event()
    speech = await playback.queue(timeout=0.03)
    assert not await asyncio.wait_for(speech.wait(), 1)
    playback.tts.gates["Hello."].set()
    assert await playback.worker.flush_pipeline(timeout=1)
    assert speech.outcome is PlaybackOutcome.TIMED_OUT
    assert not await playback.muted()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
async def test_generated_response_waits_for_its_end_at_output(playback, interrupt):
    speech = playback.engine.speech_playback.expect_response(mute_user=True)
    stopped = asyncio.Event()

    def observed(_processor, frame):
        if isinstance(frame, TTSStoppedFrame):
            stopped.set()

    playback.output.add_event_handler("on_after_push_frame", observed)
    # A provider can pause between segments of a single generated response.
    for frame in (
        LLMFullResponseStartFrame(),
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
    ):
        await playback.output.queue_frame(frame)
    await asyncio.wait_for(stopped.wait(), 1)
    assert not speech.done, "A bot stop between segments must not end the response"
    assert await playback.muted()
    if interrupt:
        await playback.output.queue_frame(InterruptionFrame())
    else:
        playback.output.writing.clear()
        playback.output.release.clear()
        for frame in (
            TTSStartedFrame(),
            TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
            TTSStoppedFrame(),
            LLMFullResponseEndFrame(),
        ):
            await playback.output.queue_frame(frame)
        await asyncio.wait_for(playback.output.writing.wait(), 1)
        assert not speech.done
        playback.output.release.set()
    assert await asyncio.wait_for(speech.wait(), 1) is (not interrupt)
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_generation_cancelled_before_start_does_not_need_a_bot_event(playback):
    speech = playback.engine.speech_playback.expect_response(mute_user=True)
    await playback.output.queue_frame(InterruptionFrame())
    assert not await asyncio.wait_for(speech.wait(), 0.5)
    assert speech.outcome is PlaybackOutcome.INTERRUPTED
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_realtime_configured_text_is_skipped_without_taking_a_mute(playback):
    playback.engine._is_realtime = True
    speech = await playback.queue()
    assert not await asyncio.wait_for(speech.wait(), 1)
    assert speech.outcome is PlaybackOutcome.SKIPPED
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_timestamped_generation_end_cannot_overtake_audio(playback):
    speech = playback.engine.speech_playback.expect_response()
    # Word-timestamp TTS assigns a presentation timestamp to the response end.
    # That frame takes the transport's clock queue, separate from audio writes.
    end = LLMFullResponseEndFrame()
    end.pts = 1
    playback.output.release.clear()
    for frame in (
        LLMFullResponseStartFrame(),
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
        end,
    ):
        await playback.output.queue_frame(frame)
    await asyncio.wait_for(playback.output.writing.wait(), 1)
    await asyncio.sleep(0.02)
    assert not speech.done
    playback.output.release.set()
    assert await asyncio.wait_for(speech.wait(), 1)


@pytest.mark.asyncio
async def test_response_already_queued_at_output_cannot_finish_next_response(playback):
    # The LLM has already emitted its current start, but output has not processed
    # it yet when a tool registers the *next* generated reply (e.g. an end node).
    await playback.tts.push_frame(LLMFullResponseStartFrame())
    speech = playback.engine.speech_playback.expect_response(source=playback.tts)
    for frame in (
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
        LLMFullResponseEndFrame(),
    ):
        await playback.tts.push_frame(frame)
    assert await playback.worker.flush_pipeline(timeout=1)
    assert not speech.done
    for frame in (
        LLMFullResponseStartFrame(),
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
        LLMFullResponseEndFrame(),
    ):
        await playback.tts.push_frame(frame)
    assert await asyncio.wait_for(speech.wait(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [PlaybackOutcome.TIMED_OUT, PlaybackOutcome.INTERRUPTED]
)
@pytest.mark.parametrize("late_start", [False, True])
@pytest.mark.parametrize("end_after_expiry", [False, True])
@pytest.mark.parametrize("source_owned", [False, True])
@pytest.mark.parametrize("playback", [{"hold_boundaries": True}], indirect=True)
async def test_stale_generation_boundaries_cannot_claim_new_response(
    playback, outcome, late_start, end_after_expiry, source_owned
):
    tracker = playback.engine.speech_playback

    old = tracker.expect_response(
        source=playback.tts, timeout=0.05 if outcome is PlaybackOutcome.TIMED_OUT else 1
    )
    old_start = await playback.emit_boundary(LLMFullResponseStartFrame())
    if not end_after_expiry:
        old_end = await playback.emit_boundary(LLMFullResponseEndFrame())
    if outcome is PlaybackOutcome.INTERRUPTED:
        await playback.output.queue_frame(InterruptionFrame())
    assert not await asyncio.wait_for(old.wait(), 1)
    assert old.outcome is outcome
    if end_after_expiry:
        old_end = await playback.emit_boundary(LLMFullResponseEndFrame())

    current = tracker.expect_response(
        source=playback.tts if source_owned else None, mute_user=True, timeout=1
    )
    current_start, current_end = LLMFullResponseStartFrame(), LLMFullResponseEndFrame()
    if source_owned:
        current_start = await playback.emit_boundary(current_start)
        current_end = await playback.emit_boundary(current_end)
    await playback.output.queue_frame(current_start)
    if late_start:
        await playback.output.queue_frame(old_start)
    await playback.output.queue_frame(old_end)
    assert await playback.worker.flush_pipeline(timeout=1)
    assert not current.done, "A stale end completed the newer response"
    assert await playback.muted()

    for frame in (
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
        current_end,
    ):
        await playback.output.queue_frame(frame)
    assert await asyncio.wait_for(current.wait(), 1)
    assert current.outcome is PlaybackOutcome.PLAYED
    assert old.outcome is outcome
    assert not await playback.muted()


@pytest.mark.asyncio
@pytest.mark.parametrize("playback", [{"hold_boundaries": True}], indirect=True)
async def test_stale_audio_does_not_make_an_empty_new_response_successful(playback):
    tracker = playback.engine.speech_playback
    old = tracker.expect_response(source=playback.tts, timeout=0.05)
    old_start = await playback.emit_boundary(LLMFullResponseStartFrame())
    old_end = await playback.emit_boundary(LLMFullResponseEndFrame())
    assert not await asyncio.wait_for(old.wait(), 1)

    current = tracker.expect_response(source=playback.tts, timeout=1)
    current_start = await playback.emit_boundary(LLMFullResponseStartFrame())
    current_end = await playback.emit_boundary(LLMFullResponseEndFrame())
    for frame in (
        current_start,
        old_start,
        TTSStartedFrame(),
        TTSAudioRawFrame(b"\x01\x00" * 640, 16000, 1),
        TTSStoppedFrame(),
        old_end,
        current_end,
    ):
        await playback.output.queue_frame(frame)
    assert not await asyncio.wait_for(current.wait(), 1)
    assert current.outcome is PlaybackOutcome.FAILED
    assert not current.has_output


@pytest.mark.asyncio
async def test_call_shutdown_resolves_speech_that_never_started(playback):
    playback.tts.gates["Hello."] = asyncio.Event()
    speech = await playback.queue(timeout=10)
    await asyncio.wait_for(playback.tts.requested.get(), 1)
    await playback.worker.queue_frame(CancelFrame())
    assert not await asyncio.wait_for(speech.wait(), 1)
    assert speech.outcome is PlaybackOutcome.CLOSED
    assert not await playback.muted()


@pytest.mark.asyncio
async def test_enqueue_failure_releases_its_mute(playback):
    playback.engine.active_agent.worker.queue_frame = AsyncMock(
        side_effect=RuntimeError("closed")
    )
    with pytest.raises(RuntimeError, match="closed"):
        await playback.queue()
    assert not playback.engine.speech_playback.pending
    assert not await playback.muted()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, RuntimeError("unavailable")])
@pytest.mark.parametrize("greeting", [False, True])
async def test_recording_preparation_failure_never_takes_a_mute(
    playback, error, greeting
):
    playback.engine.set_fetch_recording_audio(
        AsyncMock(return_value=None, side_effect=error)
    )
    speech = await playback.engine.queue_speech(
        recording_pk=1, mute_user=True, greeting=greeting
    )
    assert not await speech.wait()
    assert speech.outcome is PlaybackOutcome.FAILED
    assert not await playback.muted()
    assert not playback.engine.speech_playback.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting", [False, True])
async def test_cancelling_recording_preparation_releases_playback(playback, greeting):
    fetching = asyncio.Event()

    async def fetch(**_):
        fetching.set()
        await asyncio.Event().wait()

    playback.engine.set_fetch_recording_audio(AsyncMock(side_effect=fetch))
    preparation = asyncio.create_task(
        playback.engine.queue_speech(recording_pk=1, mute_user=True, greeting=greeting)
    )
    try:
        await asyncio.wait_for(fetching.wait(), 1)
        assert not await playback.muted()
        assert playback.engine.speech_playback.pending
    finally:
        preparation.cancel()
        await asyncio.gather(preparation, return_exceptions=True)
    assert not playback.engine.speech_playback.pending
    assert not playback.output.writing.is_set()
