"""Call-owned audio stays live while any generation service prepares."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import InterruptionFrame, LLMContextFrame
from pipecat.tests.mock_llm_service import ContextCapturingMockLLM
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.pipecat.worker_runner import run_worker_runner
from api.tests.test_agent_transfer import TransferHarness, build_agent_workflow


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


@asynccontextmanager
async def preparing_call(
    *, slow_service="tts", greeting="audio", missing_recording=False
):
    llm = ContextCapturingMockLLM(
        mock_steps=[ContextCapturingMockLLM.create_text_chunks("How can I help?")],
        chunk_delay=0,
    )
    workflow = build_agent_workflow(
        name="Reception", greeting="Welcome." if greeting == "text" else None
    )
    if greeting == "audio":
        node = workflow.nodes[workflow.start_node_id]
        node.greeting_type = "audio"
        node.greeting_recording_id = "42"
    harness = TransferHarness()
    await harness.build(
        source_llm=llm,
        destination_llm=llm,
        source_workflow=workflow,
        destination_workflow=workflow,
    )
    engine = harness.engine
    engine.set_fetch_recording_audio(
        AsyncMock(
            return_value=None
            if missing_recording
            else RecordingAudio(b"\x00\x10" * 1600, "Welcome.")
        )
    )
    service = getattr(engine.active_agent, slow_service)
    original_setup = service.setup
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def setup(config):
        await original_setup(config)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    service.setup = setup
    harness._run_task = asyncio.create_task(
        run_worker_runner(harness.runner, harness.call_worker)
    )
    try:
        await harness._await_pipeline_started()
        engine.begin_initial_agent_startup()
        await asyncio.wait_for(entered.wait(), 2)
        yield harness, release, cancelled
    finally:
        if not engine.is_call_disposed():
            await asyncio.wait_for(
                engine.end_call_with_reason(
                    EndTaskReason.PIPELINE_ERROR.value, abort_immediately=True
                ),
                3,
            )
        await asyncio.wait_for(harness._run_task, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("slow_service", ["llm", "tts"])
async def test_recorded_opening_and_inbound_audio_continue_during_setup(slow_service):
    async with preparing_call(slow_service=slow_service) as (h, release, _):
        before = h.audio_probe.input_frames
        opening = await asyncio.wait_for(
            h.engine.queue_node_opening(node_id="start", wait_for_playback=True), 2
        )
        assert opening.action == "greeting"
        assert opening.playback.log_context["node_id"] == "start"
        assert h.transport.output()._frames_written > 0
        assert h.audio_probe.input_frames > before
        assert not h.engine.generation_ready
        assert not h.engine.active_agent.llm.captured_contexts
        release.set()
        assert await asyncio.wait_for(h.engine.wait_for_generation_ready(), 2)
        assert {"role": "assistant", "content": "Welcome."} in h.engine.context.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("greeting", "missing_recording", "action"),
    [("text", False, "greeting"), (None, False, "llm"), ("audio", True, "llm")],
)
async def test_generation_and_recording_fallback_wait_for_readiness(
    greeting, missing_recording, action
):
    async with preparing_call(
        greeting=greeting, missing_recording=missing_recording
    ) as (h, release, _):
        opening = asyncio.create_task(
            h.engine.queue_node_opening(node_id="start", generate_if_no_greeting=True)
        )
        try:
            before = h.audio_probe.input_frames
            await eventually(lambda: h.audio_probe.input_frames > before + 2)
            assert not opening.done()
            assert h.transport.output()._frames_written == 0
            assert not h.engine.active_agent.llm.captured_contexts
            release.set()
            assert (await asyncio.wait_for(opening, 2)).action == action
            await eventually(lambda: h.transport.output()._frames_written > 0)
        finally:
            opening.cancel()
            await asyncio.gather(opening, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
async def test_early_caller_context_is_sent_once_after_setup_unless_interrupted(
    interrupt,
):
    async with preparing_call(greeting=None) as (h, release, _):
        engine = h.engine
        for text in ("Hello?", "I need help with my order."):
            engine.context.add_message({"role": "user", "content": text})
            await h.call_worker.queue_frame(LLMContextFrame(engine.context))
        # A flush must stay within the call while the agent is unready.
        assert await asyncio.wait_for(engine.drain_call_pipeline(), 1)
        assert not engine.active_agent.llm.captured_contexts
        if interrupt:
            await h.call_worker.queue_frame(InterruptionFrame())
            assert await asyncio.wait_for(engine.drain_call_pipeline(), 1)
        release.set()
        assert await asyncio.wait_for(engine.wait_for_generation_ready(), 2)
        if not interrupt:
            await eventually(lambda: bool(engine.active_agent.llm.captured_contexts))
            assert len(engine.active_agent.llm.captured_contexts) == 1
            messages = engine.active_agent.llm.captured_contexts[0]["messages"]
            assert [m["content"] for m in messages if m["role"] == "user"] == [
                "Hello?",
                "I need help with my order.",
            ]
            assert engine.active_agent.llm.captured_contexts[0]["system_prompt"]
        else:
            assert await asyncio.wait_for(engine.drain_call_pipeline(), 1)
            assert not engine.active_agent.llm.captured_contexts


@pytest.mark.asyncio
async def test_hangup_cancels_service_setup_without_waiting_for_its_timeout():
    async with preparing_call() as (h, _, cancelled):
        waiter = asyncio.create_task(h.engine.wait_for_generation_ready())
        await asyncio.wait_for(
            h.engine.end_call_with_reason(
                EndTaskReason.USER_HANGUP.value, abort_immediately=True
            ),
            2,
        )
        assert cancelled.is_set()
        assert not await waiter
        await eventually(h.engine.active_agent.worker.has_finished)
        assert h.engine.active_agent.current_node is None
        assert h.transport.output()._frames_written == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["worker_ended", "timeout"])
async def test_preparation_failure_ends_call_and_releases_waiters(monkeypatch, failure):
    if failure == "timeout":
        monkeypatch.setattr(
            "api.services.workflow.pipecat_engine.INITIAL_AGENT_STARTUP_TIMEOUT_SECONDS",
            0.2,
        )
    async with preparing_call() as (h, _, cancelled):
        if failure == "worker_ended":
            await h.engine.active_agent.worker.cancel(reason="failed to connect")
        assert not await asyncio.wait_for(h.engine.wait_for_generation_ready(), 2)
        await asyncio.wait_for(h._run_task, 3)
        assert h.engine.is_call_disposed()
        assert cancelled.is_set()
        assert (
            h.engine._gathered_context["call_status"]
            == EndTaskReason.PIPELINE_ERROR.value
        )
        assert h.transport.output()._frames_written == 0


@pytest.mark.asyncio
async def test_a_second_call_can_generate_while_first_call_is_preparing():
    async with (
        preparing_call() as (first, _, _),
        preparing_call(greeting=None) as (second, release, _),
    ):
        before = first.audio_probe.input_frames
        release.set()
        assert await asyncio.wait_for(second.engine.wait_for_generation_ready(), 2)
        await second.engine.queue_node_opening(
            node_id="start", generate_if_no_greeting=True
        )
        await eventually(lambda: second.transport.output()._frames_written > 0)
        assert first.audio_probe.input_frames > before
        assert not first.engine.generation_ready
