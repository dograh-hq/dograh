"""Response failure recovery across real child-worker, TTS and output queues."""

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    DataFrame,
    ErrorFrame,
    FunctionCallResultProperties,
    HeartbeatFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    OutputAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.filters.identity_filter import IdentityFilter
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat.agent_bridge import AgentBridgeProcessor
from api.services.pipecat.call_monitor_processor import CallMonitorProcessor
from api.services.pipecat.pipeline_builder import (
    build_realtime_pipeline,
    create_agent_worker,
)
from api.services.pipecat.worker_runner import (
    create_worker_runner,
    run_worker_runner,
    wait_for_pipeline_worker_started,
)
from api.services.workflow.agent_transfer import TransferPhase
from api.services.workflow.pipecat_engine import PipecatEngine
from pipecat.tests import MockLLMService, MockTTSService


class ControlledLLM(MockLLMService):
    def __init__(self):
        super().__init__(mock_chunks=self.create_text_chunks("Hello."), chunk_delay=0)
        self.mode = "audio"
        self.requested = asyncio.Event()
        self.release = asyncio.Event()

    async def process_frame(self, frame, direction):
        if type(frame) is DataFrame and self.mode == "queued":
            self.requested.set()
            await self.release.wait()
        if isinstance(frame, LLMContextFrame) and self.mode == "before_start":
            self.requested.set()
            await self.release.wait()
        await super().process_frame(frame, direction)

    async def get_chat_completions(self, context):
        self.requested.set()
        if self.mode == "error":
            raise RuntimeError("simulated provider failure")
        if self.mode == "stalled":
            await self.release.wait()
        if self.mode == "empty":
            self._mock_chunks = []
        chunks = await super().get_chat_completions(context)
        if self.mode == "audio_then_stall":

            async def stall_after_text():
                async for chunk in chunks:
                    yield chunk
                await self.release.wait()

            return stall_after_text()
        return chunks


class ControlledTTS(MockTTSService):
    def __init__(self):
        super().__init__(mock_audio_duration_ms=400, frame_delay=0)
        self.fail = False

    async def run_tts(self, text, context_id):
        if self.fail:
            yield ErrorFrame("simulated TTS failure")
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
        self.release = asyncio.Event()
        self.release.set()
        self.writing = asyncio.Event()
        self.events = []
        self.add_event_handler("on_after_push_frame", self.observe)

    def observe(self, _processor, frame):
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
            self.events.append(type(frame))

    async def write_audio_frame(self, frame):
        self.writing.set()
        await self.release.wait()
        return await super().write_audio_frame(frame)


class ResponseHarness:
    def __init__(self, *, split=True):
        self.runner = create_worker_runner()
        self.llm = ControlledLLM()
        self.tts = ControlledTTS()
        self.output = ControlledOutput()
        self.context = LLMContext()
        self.engine = PipecatEngine(
            llm=self.llm,
            context=self.context,
            workflow=None,
            call_context_vars={},
            is_realtime=not split,
        )
        self.engine.active_agent.is_child = split
        self.engine.active_agent.worker = None
        self.engine.response_watchdog.response_timeout = 0.15
        self.engine.response_watchdog.tool_timeout = 0.5
        self.user, self.assistant = LLMContextAggregatorPair(
            self.context,
            user_params=LLMUserAggregatorParams(user_idle_timeout=0.1),
            realtime_service_mode=False,
        )
        self.idle = asyncio.Event()
        self.idle_handler = self.engine.create_user_idle_handler()
        self.handle_idle = False

        @self.user.event_handler("on_user_turn_idle")
        async def on_idle(aggregator):
            self.idle.set()
            if self.handle_idle:
                await self.idle_handler.handle_idle(aggregator)

        self.bridge = AgentBridgeProcessor(
            bus=self.runner.bus,
            worker_name="watchdog-call",
            selected_visit=lambda: self.engine.selected_visit_id,
            allow_inference=lambda: not self.engine.transfer_in_progress,
        )
        self.monitor = CallMonitorProcessor(
            response_watchdog=self.engine.response_watchdog,
            response_source=lambda: (
                None
                if self.engine.transfer_in_progress
                else self.engine.active_agent.llm
            ),
            max_duration_end_task_callback=self.engine.create_max_duration_callback(),
        )
        if split:
            pipeline = Pipeline(
                [self.user, self.monitor, self.bridge, self.output, self.assistant]
            )
        else:
            input_processor = IdentityFilter()
            # Exercise the production realtime ordering with a controlled LLM
            # and audio stub standing in for the speech-to-speech provider.
            pipeline = build_realtime_pipeline(
                transport=SimpleNamespace(
                    input=lambda: input_processor, output=lambda: self.output
                ),
                realtime_llm=self.llm,
                audio_buffer=IdentityFilter(),
                user_context_aggregator=self.user,
                assistant_context_aggregator=self.assistant,
                call_monitor_processor=self.monitor,
                agent_generation_processor=self.tts,
                pipeline_metrics_aggregator=IdentityFilter(),
                termination_funnel=IdentityFilter(),
            )
        self.worker = PipelineWorker(
            pipeline,
            name="watchdog-call",
            params=PipelineParams(audio_out_sample_rate=16000),
            enable_rtvi=False,
            idle_timeout_secs=None,
        )
        self.child = (
            create_agent_worker(
                Pipeline([self.llm, self.tts]),
                name=self.engine.active_agent.visit_id,
                call_worker_name=self.worker.name,
                audio_config=SimpleNamespace(
                    transport_in_sample_rate=16000, transport_out_sample_rate=16000
                ),
            )
            if split
            else self.worker
        )
        self.engine.active_agent.worker = self.child
        self.engine.call_worker = self.worker
        self.engine.set_transport_output(self.output)

    async def start(self):
        self.task = asyncio.create_task(run_worker_runner(self.runner, self.worker))
        await wait_for_pipeline_worker_started(
            self.worker, timeout=10, run_task=self.task
        )
        if self.engine.active_agent.is_child:
            await self.worker.add_workers(self.child)
            await wait_for_pipeline_worker_started(self.child, timeout=3)
        assert await self.engine.activate_agent(self.engine.active_agent, timeout=1)

    async def request(self, *, speculative=False):
        await self.worker.queue_frame(
            LLMContextFrame(self.context, speculation=speculative)
        )
        await asyncio.wait_for(self.llm.requested.wait(), 1)

    async def assert_aborted(self):
        await asyncio.wait_for(asyncio.shield(self.task), 2)
        assert (
            self.engine._gathered_context["call_status"]
            == EndTaskReason.PIPELINE_ERROR.value
        )
        assert self.child.has_finished()
        assert not self.engine.speech_playback.pending


@pytest.fixture
async def response(request):
    harness = ResponseHarness(split=getattr(request, "param", True))
    try:
        await harness.start()
        yield harness
    finally:
        harness.llm.release.set()
        harness.output.release.set()
        harness.engine.speech_playback.cancel_all()
        if not harness.task.done():
            await harness.engine.end_call_with_reason(
                EndTaskReason.PIPELINE_ERROR.value, abort_immediately=True
            )
        await asyncio.wait_for(harness.task, 3)
        if harness.engine._user_response_timeout_task:
            await harness.engine._user_response_timeout_task


@pytest.mark.parametrize("mode", ["error", "empty", "stalled", "before_start"])
@pytest.mark.parametrize("response", [True, False], indirect=True)
async def test_silent_response_ends_call_without_any_bot_event(response, mode):
    response.llm.mode = mode
    await response.request()
    await response.assert_aborted()
    assert not response.output.events
    assert not response.idle.is_set()


async def test_failed_tts_ends_call_without_bot_events(response):
    response.tts.fail = True
    await response.request()
    await response.assert_aborted()
    assert not response.output.events


@pytest.mark.parametrize("response", [True, False], indirect=True)
async def test_successful_long_audio_renews_deadline_and_arms_normal_idle(response):
    await response.request()
    await asyncio.wait_for(response.idle.wait(), 2)
    assert BotStoppedSpeakingFrame in response.output.events
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()
    assert not response.engine.response_watchdog.pending_response


async def test_failed_idle_reminder_also_has_a_deadline(response):
    await response.request()
    await asyncio.wait_for(response.output.writing.wait(), 1)
    response.llm.mode = "error"
    response.handle_idle = True
    await response.assert_aborted()
    assert response.idle_handler._retry_count == 1
    # The transport broadcasts each speaking event upstream and downstream.
    assert response.output.events.count(BotStartedSpeakingFrame) == 2


async def test_bot_start_before_a_stuck_transport_write_is_not_progress(response):
    response.output.release.clear()
    await response.request()
    await asyncio.wait_for(response.output.writing.wait(), 1)
    await response.assert_aborted()


@pytest.mark.parametrize("response", [True, False], indirect=True)
async def test_request_queued_behind_a_stuck_processor_is_still_watched(response):
    response.llm.mode = "queued"
    await response.child.queue_frame(DataFrame())
    await asyncio.wait_for(response.llm.requested.wait(), 1)
    await response.request()
    await response.assert_aborted()
    assert not response.output.events


@pytest.mark.parametrize("response", [True, False], indirect=True)
async def test_call_duration_limit_still_applies_while_generation_is_stalled(response):
    response.engine.response_watchdog.response_timeout = 10
    response.monitor._max_call_duration_seconds = 0
    response.llm.mode = "stalled"
    await response.request()
    await response.worker.queue_frame(HeartbeatFrame(timestamp=0))
    await asyncio.wait_for(asyncio.shield(response.task), 2)
    assert (
        response.engine._gathered_context["call_status"]
        == EndTaskReason.CALL_DURATION_EXCEEDED.value
    )
    assert not response.engine.response_watchdog.pending_response


async def test_response_stalling_after_partial_audio_is_bounded(response):
    response.llm.mode = "audio_then_stall"
    await response.request()
    await asyncio.wait_for(response.output.writing.wait(), 1)
    await response.assert_aborted()
    assert BotStartedSpeakingFrame in response.output.events


async def test_silent_retries_cannot_keep_resetting_the_response_budget(response):
    response.engine.response_watchdog.response_timeout = 0.25
    response.llm.mode = "empty"
    await response.request()
    for _ in range(3):
        await asyncio.sleep(0.06)
        await response.worker.queue_frame(LLMContextFrame(response.context))
    # If retries reset the clock, this would take another full 250 ms.
    await asyncio.wait_for(asyncio.shield(response.task), 0.15)
    await response.assert_aborted()
    assert not response.output.events


async def test_late_old_response_end_cannot_cancel_new_response_watch(response):
    old_ends = []

    def capture(_source, frame):
        if isinstance(frame, LLMFullResponseEndFrame):
            old_ends.append(frame)

    response.llm.add_event_handler("on_before_push_frame", capture)
    await response.request()
    await asyncio.wait_for(response.idle.wait(), 2)
    assert old_ends
    response.llm.mode = "stalled"
    response.llm.requested.clear()
    await response.request()
    await response.output.queue_frame(old_ends[0])
    await response.assert_aborted()


async def test_cancelled_speculation_does_not_arm_a_response_timeout(response):
    response.llm.mode = "stalled"
    await response.request(speculative=True)
    await response.worker.queue_frame(InterruptionFrame())
    assert await response.worker.flush_pipeline(timeout=1)
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()


async def test_answer_screening_does_not_arm_a_response_timeout(response):
    response.engine.answer_supervisor = SimpleNamespace(blocks_workflow=True)
    response.llm.mode = "empty"
    await response.request()
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()
    assert not response.engine.response_watchdog.pending_response


async def test_interrupted_request_cannot_end_the_next_turn(response):
    response.llm.mode = "stalled"
    await response.request()
    await response.worker.queue_frame(InterruptionFrame())
    assert await response.worker.flush_pipeline(timeout=1)
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()
    response.llm.mode = "audio"
    response.llm.requested.clear()
    await response.request()
    await asyncio.wait_for(response.idle.wait(), 2)
    assert not response.engine.is_call_disposed()


async def test_speculation_only_starts_deadline_when_turn_is_confirmed(response):
    response.llm.mode = "stalled"
    await response.request(speculative=True)
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()
    await response.worker.queue_frame(UserStoppedSpeakingFrame())
    await response.assert_aborted()


async def test_hold_audio_does_not_count_as_a_response(response):
    response.llm.mode = "stalled"
    await response.request()
    await response.output.queue_frame(OutputAudioRawFrame(b"\x01\x00" * 6400, 16000, 1))
    await response.assert_aborted()


async def test_deactivation_cancels_watch_and_ignores_retired_inference(response):
    response.llm.mode = "stalled"
    await response.request()
    await response.engine.deactivate_agent(response.engine.active_agent)
    response.engine.watch_inference_frame(LLMContextFrame(response.context))
    await asyncio.sleep(0.2)
    assert not response.engine.is_call_disposed()
    assert not response.engine.response_watchdog.pending_response


@pytest.mark.parametrize("phase", [TransferPhase.PREPARING, TransferPhase.OPENING])
async def test_transfer_gated_request_does_not_arm_response_deadline(response, phase):
    response.engine._transfer_coordinator = SimpleNamespace(
        in_progress=True, phase=phase
    )
    try:
        await response.worker.queue_frame(LLMContextFrame(response.context))
        await asyncio.sleep(0.2)
        assert not response.llm.requested.is_set()
        assert not response.engine.response_watchdog.pending_response
        assert not response.engine.is_call_disposed()
    finally:
        response.engine._transfer_coordinator = None


@pytest.mark.parametrize("continuation", ["audio", "empty", "stuck_tool"])
@pytest.mark.parametrize("preamble", [False, True])
async def test_tool_wait_and_followup_remain_bounded(response, continuation, preamble):
    tool_started = asyncio.Event()

    async def lookup(params):
        tool_started.set()
        if continuation == "stuck_tool":
            await asyncio.Event().wait()
        # Longer than the first-audio budget, shorter than the tool budget.
        await asyncio.sleep(0.25)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties()
        )

    response.llm.register_function("lookup", lookup)
    response.llm._mock_steps = [
        (
            MockLLMService.create_mixed_chunks(
                "Let me check.", "lookup", {}, "lookup-1"
            )
            if preamble
            else MockLLMService.create_function_call_chunks("lookup", {}, "lookup-1")
        ),
        (
            MockLLMService.create_text_chunks("Found it.")
            if continuation == "audio"
            else []
        ),
    ]
    await response.request()
    await asyncio.wait_for(tool_started.wait(), 1)
    if continuation == "audio":
        await asyncio.wait_for(response.idle.wait(), 2)
        assert not response.engine.is_call_disposed()
    else:
        await response.assert_aborted()
        if not preamble:
            assert not response.output.events
