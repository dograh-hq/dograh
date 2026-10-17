"""Opening policy across cascade and realtime, without provider network calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    StopFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.call_opening import OpeningInputGate
from api.services.pipecat.speech_playback import PlaybackOutcome
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.test_call_start_sequence import Call

D = FrameDirection.DOWNSTREAM


def gate():
    value = OpeningInputGate()
    value.push_frame = AsyncMock()
    return value


@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.asyncio
async def test_delayed_start_is_from_connection_even_when_pipeline_starts_late(
    monkeypatch, realtime
):
    call = Call(monkeypatch, fetch_task=None, is_child=not realtime)
    engine = call.engine
    engine._is_realtime = realtime
    engine.active_agent.workflow.nodes["start"] = SimpleNamespace(
        delayed_start=True,
        delayed_start_duration=0.2,
        greeting=None,
        greeting_type="text",
        is_start=True,
    )
    await call.transport.handlers["on_client_connected"](call.transport, None)
    # Simulate time spent starting the pipeline before its readiness callback.
    engine.opening.deadline.armed_at -= 0.3
    await asyncio.wait_for(
        call.task.handlers["on_pipeline_started"](call.task, None), 0.1
    )
    engine.queue_node_opening.assert_awaited_once()
    await engine.opening.close()


@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.asyncio
async def test_delayed_start_waits_and_closing_prevents_opening(monkeypatch, realtime):
    call = Call(monkeypatch, fetch_task=None, is_child=not realtime)
    call.engine._is_realtime = realtime
    call.engine.active_agent.workflow.nodes["start"] = SimpleNamespace(
        delayed_start=True,
        delayed_start_duration=10,
    )
    started = await call.ready()
    try:
        await asyncio.sleep(0.02)
        call.engine.queue_node_opening.assert_not_awaited()
        await call.engine.opening.close()
        await asyncio.wait_for(started, 0.2)
        call.engine.queue_node_opening.assert_not_awaited()
    finally:
        started.cancel()
        await asyncio.gather(started, return_exceptions=True)


@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.asyncio
async def test_caller_first_releases_retained_input_without_second_greeting(
    monkeypatch, realtime
):
    call = Call(monkeypatch, fetch_task=None, is_child=not realtime)
    engine = call.engine
    engine._is_realtime = realtime
    engine.active_agent.workflow.nodes["start"] = SimpleNamespace(
        delayed_start=True,
        delayed_start_duration=0.01,
    )
    engine.opening.input_gate = retained = gate()
    if realtime:
        retained._vad = SimpleNamespace(
            analyze_audio=AsyncMock(return_value=VADState.SPEAKING)
        )
    audio = InputAudioRawFrame(b"\x01\x02" * 320, 16000, 1)
    await retained.process_frame(audio, D)
    if not realtime:
        await retained.process_frame(TranscriptionFrame("Hello?", "caller", ""), D)
    service = SimpleNamespace(
        handle_prerecorded_greeting=AsyncMock(),
        wait_for_input_ready=AsyncMock(),
    )
    if realtime:
        engine.active_agent.llm = service
    await asyncio.wait_for(await call.ready(), 1)
    async with asyncio.timeout(1):
        while retained.push_frame.await_count != (1 if realtime else 2):
            await asyncio.sleep(0)
    engine.queue_node_opening.assert_not_awaited()
    assert retained.push_frame.await_args_list[0].args[0] is audio
    if realtime:
        service.handle_prerecorded_greeting.assert_awaited_once_with(
            engine.context, None
        )
        service.wait_for_input_ready.assert_awaited_once()
    await engine.opening.close()


@pytest.mark.asyncio
async def test_provider_readiness_timeout_ends_call_and_retains_input_until_cleanup(
    monkeypatch,
):
    monkeypatch.setattr(
        "api.services.workflow.pipecat_engine.INITIAL_AGENT_STARTUP_TIMEOUT_SECONDS",
        0.01,
    )
    engine = PipecatEngine(workflow=None, call_context_vars={}, is_realtime=True)
    engine.opening.input_gate = retained = gate()
    engine.active_agent.llm = SimpleNamespace(wait_for_input_ready=asyncio.Event().wait)
    engine.end_call_with_reason = AsyncMock()
    await retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    await engine.release_opening_input()
    engine.end_call_with_reason.assert_awaited_once_with("pipeline_error")
    retained.push_frame.assert_not_awaited()
    await engine.opening.close()


@pytest.mark.asyncio
async def test_opening_task_can_close_itself_without_self_cancellation():
    engine = PipecatEngine(workflow=None, call_context_vars={})
    completed = asyncio.Event()

    async def close():
        await engine.opening.close()
        completed.set()

    engine.opening.prepare(close())
    await asyncio.wait_for(completed.wait(), 1)
    preparation = AsyncMock()
    engine.opening.prepare(preparation())
    await asyncio.sleep(0)
    preparation.assert_not_awaited()


@pytest.mark.asyncio
async def test_input_replay_preserves_order_when_new_audio_arrives_during_release():
    retained = gate()
    first = InputAudioRawFrame(bytes(640), 16000, 1)
    second = InputAudioRawFrame(bytes(320), 8000, 1)
    await retained.process_frame(first, D)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def send(frame, direction):
        if frame is first:
            entered.set()
            await resume.wait()

    retained.push_frame.side_effect = send
    release = asyncio.create_task(retained.release())
    incoming = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        incoming = asyncio.create_task(retained.process_frame(second, D))
        await asyncio.sleep(0)
        assert not incoming.done()
        resume.set()
        await asyncio.wait_for(asyncio.gather(release, incoming), 1)
        assert [call.args[0] for call in retained.push_frame.await_args_list] == [
            first,
            second,
        ]
    finally:
        resume.set()
        tasks = [release] + ([incoming] if incoming else [])
        for task in tasks:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)


@pytest.mark.asyncio
async def test_cascade_retains_turn_boundaries_with_their_transcript():
    retained = gate()
    frames = [
        ProposedUserStartedSpeakingFrame(),
        TranscriptionFrame("Hello", "caller", ""),
        ProposedUserStoppedSpeakingFrame(),
    ]
    for frame in frames:
        await retained.process_frame(frame, D)
    assert retained.heard_speech
    retained.push_frame.assert_not_awaited()
    await retained.release()
    assert [call.args[0] for call in retained.push_frame.await_args_list] == frames


@pytest.mark.parametrize("terminal", [CancelFrame, EndFrame, StopFrame])
@pytest.mark.asyncio
async def test_terminal_frames_discard_retained_input_and_pass_immediately(terminal):
    retained = gate()
    await retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    end = terminal()
    await retained.process_frame(end, D)
    await retained.release()
    await retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    retained.push_frame.assert_awaited_once_with(end, D)


@pytest.mark.asyncio
async def test_input_retention_overflow_is_reported_instead_of_silently_losing_speech():
    retained = OpeningInputGate(max_audio_seconds=0.01)
    retained.push_frame = AsyncMock()
    retained.push_error = AsyncMock()
    await retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    await retained.release()
    retained.push_frame.assert_not_awaited()
    retained.push_error.assert_awaited_once()
    assert retained.push_error.call_args.kwargs["force_treat_as_permanent"]


@pytest.mark.parametrize("allow_interrupt", [False, True])
@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.asyncio
async def test_input_waits_for_readiness_and_protected_playback_in_both_pipelines(
    allow_interrupt,
    realtime,
):
    engine = PipecatEngine(workflow=None, call_context_vars={}, is_realtime=realtime)
    ready = asyncio.Event()
    engine.active_agent.llm = SimpleNamespace(wait_for_input_ready=ready.wait)
    engine.wait_for_generation_ready = AsyncMock(side_effect=ready.wait)
    engine.opening.input_gate = retained = gate()
    audio = InputAudioRawFrame(b"\x01\x02" * 320, 16000, 1)
    await retained.process_frame(audio, D)
    speech = engine.speech_playback.create(greeting=True)
    speech.allow_interrupt = allow_interrupt
    release = asyncio.create_task(engine.release_opening_input(speech))
    try:
        await asyncio.sleep(0)
        retained.push_frame.assert_not_awaited()
        ready.set()
        await asyncio.sleep(0.01)
        if not allow_interrupt:
            retained.push_frame.assert_not_awaited()
            speech.finish(PlaybackOutcome.PLAYED)
        await asyncio.wait_for(release, 1)
        retained.push_frame.assert_awaited_once_with(audio, D)
    finally:
        release.cancel()
        await asyncio.gather(release, return_exceptions=True)
        engine.speech_playback.cancel_all()
        await engine.opening.close()


@pytest.mark.parametrize("suspended_in", ["capture", "vad"])
@pytest.mark.asyncio
async def test_close_during_audio_preparation_cannot_retain_a_frame(suspended_in):
    entered, resume = asyncio.Event(), asyncio.Event()

    async def suspended(_audio):
        entered.set()
        await resume.wait()
        return VADState.SPEAKING

    retained = OpeningInputGate(
        capture_audio=suspended if suspended_in == "capture" else None,
        vad=SimpleNamespace(analyze_audio=suspended) if suspended_in == "vad" else None,
    )
    retained.push_frame = AsyncMock()
    incoming = asyncio.create_task(
        retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        retained.close()
        resume.set()
        await asyncio.wait_for(incoming, 1)
        assert not retained._frames
        assert retained._audio_seconds == 0
        assert not retained.heard_speech
        await retained.release()
        retained.push_frame.assert_not_awaited()
    finally:
        resume.set()
        incoming.cancel()
        await asyncio.wait_for(asyncio.gather(incoming, return_exceptions=True), 1)


@pytest.mark.asyncio
async def test_close_cancels_warming_and_input_readiness_without_replaying_audio():
    engine = PipecatEngine(workflow=None, call_context_vars={}, is_realtime=True)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def warming():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.opening.input_gate = retained = gate()
    engine.active_agent.llm = SimpleNamespace(wait_for_input_ready=asyncio.Event().wait)
    await retained.process_frame(InputAudioRawFrame(bytes(640), 16000, 1), D)
    engine.opening.prepare(warming())
    engine.opening.prepare(engine.release_opening_input())
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        await asyncio.wait_for(engine.opening.close(), 0.2)
    assert cancelled.is_set()
    retained.push_frame.assert_not_awaited()
    assert not engine.opening._preparation
