"""Replay provider wire events through real response and transport boundaries."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat.call_opening import OpeningInputGate
from api.services.pipecat.realtime.azure_realtime import DograhAzureRealtimeLLMService
from api.services.pipecat.realtime.grok_realtime import DograhGrokRealtimeLLMService
from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from api.services.pipecat.speech_playback import PlaybackOutcome
from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.test_speech_playback_pipeline import ControlledOutput
from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection


def response(kind, response_id, *, status="completed"):
    return {
        "type": kind,
        "response": {
            "id": response_id,
            "object": "realtime.response",
            "status": "in_progress" if kind == "response.created" else status,
            "status_details": None,
            "output": [],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "input_token_details": {},
                "output_token_details": {},
            },
        },
    }


def item(response_id, *, kind="conversation.item.added", suffix="a"):
    return {
        "type": kind,
        "response_id": response_id,
        "output_index": 0,
        "previous_item_id": None,
        "item": {
            "id": f"{response_id}-{suffix}",
            "type": "message",
            "role": "assistant",
            "content": [],
        },
    }


def audio(response_id, sample_rate, *, suffix="a"):
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "item_id": f"{response_id}-{suffix}",
        "output_index": 0,
        "content_index": 0,
        "delta": base64.b64encode(b"\x01\x00" * (sample_rate * 12 // 100)).decode(),
    }


def audio_done(response_id):
    return {
        "type": "response.output_audio.done",
        "response_id": response_id,
        "item_id": f"{response_id}-a",
        "output_index": 0,
        "content_index": 0,
    }


@pytest.fixture(params=["grok", "openai", "azure"])
async def realtime_playback(request):
    if request.param == "grok":
        service = DograhGrokRealtimeLLMService(api_key="test")
    elif request.param == "azure":
        service = DograhAzureRealtimeLLMService(
            api_key="test", base_url="wss://example.test/openai/v1/realtime"
        )
    else:
        service = DograhOpenAIRealtimeLLMService(api_key="test")
    # Replace network I/O only. Run the actual parsers, handlers, frame hooks,
    # speech tracker and the transport's media queue.
    service._connect = AsyncMock()
    service._ws_send = AsyncMock()
    service._context = LLMContext()
    service._api_session_ready = True
    service._handled_initial_context = True
    service._llm_needs_conversation_setup = False
    output = ControlledOutput(sample_rate=24000)
    engine = PipecatEngine(
        workflow=None, call_context_vars={}, llm=service, is_realtime=True
    )
    engine.set_transport_output(output)
    worker = PipelineWorker(Pipeline([service, output]), enable_rtvi=False)
    engine.call_worker = worker
    ready = asyncio.Event()
    frames = []

    @worker.event_handler("on_pipeline_started")
    async def started(*_):
        ready.set()

    @service.event_handler("on_before_push_frame")
    async def emitted(_processor, frame):
        frames.append(frame)

    async def replay(*messages):
        async def stream():
            for index, message in enumerate(messages):
                yield json.dumps({"event_id": f"event-{index}", **message})

        service._websocket = stream()
        try:
            await service._receive_task_handler()
        finally:
            service._websocket = None

    runner = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await asyncio.wait_for(ready.wait(), 3)
        rate = service._get_output_sample_rate() if request.param == "grok" else 24000
        yield SimpleNamespace(
            service=service,
            engine=engine,
            tracker=engine.speech_playback,
            output=output,
            worker=worker,
            frames=frames,
            replay=replay,
            rate=rate,
        )
    finally:
        output.release.set()
        if not runner.done():
            await worker.queue_frame(EndFrame())
        await asyncio.wait_for(runner, 3)
        await engine.opening.close()


@pytest.mark.asyncio
async def test_protected_realtime_greeting_releases_retained_input_after_playback(
    realtime_playback,
):
    h = realtime_playback
    speech = h.tracker.expect_response(source=h.service, greeting=True)
    speech.allow_interrupt = False
    gate = h.engine.opening.input_gate = OpeningInputGate()
    gate.push_frame = AsyncMock()
    caller = InputAudioRawFrame(b"\x01\x02" * 1600, 16000, 1)
    await gate.process_frame(caller, FrameDirection.DOWNSTREAM)
    release_input = asyncio.create_task(h.engine.release_opening_input(speech))
    h.output.release.clear()
    try:
        # The request and both Grok item announcements used to emit separate
        # starts. A second assistant item must also remain in the same reply.
        await h.service._send_manual_response_create()
        await h.replay(
            response("response.created", "greeting"),
            response("response.created", "greeting"),
            item("greeting", kind="response.output_item.added"),
            item("greeting"),
            audio("greeting", h.rate),
            item("greeting", suffix="b"),
            audio("greeting", h.rate, suffix="b"),
            audio_done("greeting"),
            response("response.done", "greeting"),
        )
        await asyncio.wait_for(h.output.writing.wait(), 1)
        assert not speech.done
        assert not release_input.done()
        gate.push_frame.assert_not_awaited()
        h.output.release.set()
        assert await asyncio.wait_for(speech.wait(), 1)
        await asyncio.wait_for(release_input, 1)
        assert speech.first_output_at is not None
        assert speech.played_seconds == pytest.approx(0.24)
        assert speech.queued_seconds == pytest.approx(0.24)
        assert h.tracker._input_scopes == h.tracker._output_scopes == []
        assert sum(isinstance(f, LLMFullResponseStartFrame) for f in h.frames) == 1
        assert sum(isinstance(f, LLMFullResponseEndFrame) for f in h.frames) == 1
        assert any(
            call.args[0].audio == caller.audio
            for call in gate.push_frame.await_args_list
        )
    finally:
        h.output.release.set()
        release_input.cancel()
        await asyncio.gather(release_input, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_cancelled_events_cannot_finish_or_speak_over_next_response(
    realtime_playback,
):
    h = realtime_playback
    old = h.tracker.expect_response(source=h.service)
    tool = item("old", kind="response.output_item.added", suffix="tool")
    tool["item"].update(
        type="function_call", call_id="old-call", name="lookup", arguments="{}"
    )
    await h.replay(
        response("response.created", "old"),
        item("old"),
        tool,
        audio("old", h.rate),
    )
    assert await h.worker.flush_pipeline(timeout=1)
    await h.service.queue_frame(InterruptionFrame())
    assert not await asyncio.wait_for(old.wait(), 1)
    assert old.outcome is PlaybackOutcome.INTERRUPTED
    assert await h.worker.flush_pipeline(timeout=1)

    current = h.tracker.expect_response(source=h.service)
    await h.replay(response("response.created", "current"), item("current"))
    audio_count = sum(isinstance(f, TTSAudioRawFrame) for f in h.frames)
    h.service.start_llm_usage_metrics = AsyncMock()
    h.service.run_function_calls = AsyncMock()
    await h.replay(
        audio("old", h.rate),
        item("old"),
        {
            "type": "response.function_call_arguments.done",
            "response_id": "old",
            "item_id": "old-tool",
            "output_index": 0,
            "call_id": "old-call",
            "name": "lookup",
            "arguments": "{}",
        },
        response("response.done", "old", status="cancelled"),
        response("response.done", "old", status="cancelled"),
    )
    assert await h.worker.flush_pipeline(timeout=1)
    assert not current.done
    assert not current.has_output
    assert sum(isinstance(f, TTSAudioRawFrame) for f in h.frames) == audio_count
    assert h.service._current_assistant_response.id == "current-a"
    h.service.start_llm_usage_metrics.assert_awaited_once()
    h.service.run_function_calls.assert_not_awaited()

    await h.replay(
        audio("current", h.rate),
        audio_done("current"),
        response("response.done", "current"),
    )
    assert await asyncio.wait_for(current.wait(), 1)
    assert current.played_seconds == pytest.approx(0.12)
    assert h.tracker._input_scopes == h.tracker._output_scopes == []


@pytest.mark.asyncio
async def test_interruption_before_response_created_discards_the_late_response(
    realtime_playback,
):
    h = realtime_playback
    old = h.tracker.expect_response(source=h.service)
    await h.service._send_manual_response_create()
    await h.service.queue_frame(InterruptionFrame())
    assert not await asyncio.wait_for(old.wait(), 1)
    assert await h.worker.flush_pipeline(timeout=1)
    await h.replay(
        response("response.created", "old"),
        item("old"),
        audio("old", h.rate),
        response("response.done", "old", status="cancelled"),
    )
    assert not any(isinstance(f, TTSAudioRawFrame) for f in h.frames)
    # Only the original request opened a scope; the late acknowledgement must
    # not reopen it after cancellation.
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in h.frames) == 1

    current = h.tracker.expect_response(source=h.service)
    await h.service._send_manual_response_create()
    await h.replay(
        response("response.created", "current"),
        item("current"),
        audio("current", h.rate),
        audio_done("current"),
        response("response.done", "current"),
    )
    assert await asyncio.wait_for(current.wait(), 1)


@pytest.mark.asyncio
async def test_late_acknowledgement_cannot_claim_a_new_speech_expectation(
    realtime_playback,
):
    h = realtime_playback
    old = h.tracker.expect_response(source=h.service)
    await h.service._send_manual_response_create()
    old.finish(PlaybackOutcome.TIMED_OUT)
    current = h.tracker.expect_response(source=h.service)
    await h.replay(
        response("response.created", "old"),
        item("old"),
        audio("old", h.rate),
        audio_done("old"),
        response("response.done", "old"),
    )
    assert await h.worker.flush_pipeline(timeout=1)
    assert not current.done
    assert not current.has_output
    await h.service._send_manual_response_create()
    await h.replay(
        response("response.created", "current"),
        item("current"),
        audio("current", h.rate),
        audio_done("current"),
        response("response.done", "current"),
    )
    assert await asyncio.wait_for(current.wait(), 1)
    assert current.played_seconds == pytest.approx(0.12)
