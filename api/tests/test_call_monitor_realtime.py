"""Real Gemini frame semantics with local audio and no provider connection."""

import asyncio
from types import SimpleNamespace

import pytest
from google.genai.types import Blob, Content, LiveServerContent, LiveServerMessage, Part
from pipecat.frames.frames import (
    CancelFrame,
    LLMContextFrame,
    SpeechBoundaryFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.filters.identity_filter import IdentityFilter

from api.services.pipecat.call_monitor_processor import CallMonitorProcessor
from api.services.pipecat.pipeline_builder import build_realtime_pipeline
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.speech_playback import SpeechPlaybackTracker
from api.services.pipecat.worker_runner import (
    create_worker_runner,
    run_worker_runner,
    wait_for_pipeline_worker_started,
)
from api.tests.test_call_monitor import ControlledOutput


class OfflineGemini(DograhGeminiLiveLLMService):
    """Keep the production handlers; only disable provider client creation."""

    def create_client(self):
        self._client = SimpleNamespace(aio=SimpleNamespace(live=None))


@pytest.mark.parametrize(
    "turn_timing", ["before_response", "after_response", "inference_first"]
)
@pytest.mark.parametrize("transcript_flush_delay", [0.02, 10])
async def test_gemini_transcript_sync_does_not_request_another_response(
    turn_timing, transcript_flush_delay
):
    service = OfflineGemini(api_key="test-key")
    context = LLMContext()
    service._context = context
    service._handled_initial_context = True
    output = ControlledOutput()
    user, assistant = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_idle_timeout=0),
        realtime_service_mode=True,
    )
    # Exercise both the deferred transcript flush during generation and the
    # failsafe flush at response end. Both emit a context synchronization frame.
    user._ttfs_p99_latency = transcript_flush_delay
    expired = asyncio.Event()
    idle = asyncio.Event()
    drained = asyncio.Event()
    synced = asyncio.Event()

    async def on_idle(_attempt):
        idle.set()
        monitor.cancel()  # Observe the decision without requesting a reminder.

    monitor = CallMonitorProcessor(
        response_source=lambda: service,
        on_response_timeout=lambda _source: expired.set(),
        on_user_idle=on_idle,
        conversation_enabled=lambda: True,
        response_timeout=0.2,
    )
    monitor.bind_source(service, enabled=lambda: True)
    monitor.bind_user(user, idle_timeout=0.1)
    playback = SpeechPlaybackTracker()
    playback.add_observer(monitor)
    playback.observe_responses(service)
    playback.bind_output(output)

    def observe_context(_source, frame):
        if isinstance(frame, LLMContextFrame):
            synced.set()

    def observe_output(_output, frame):
        if isinstance(frame, SpeechBoundaryFrame) and not frame.beginning:
            drained.set()

    service.add_event_handler("on_before_process_frame", observe_context)
    output.add_event_handler("on_after_push_frame", observe_output)
    pipeline = build_realtime_pipeline(
        transport=SimpleNamespace(input=IdentityFilter, output=lambda: output),
        realtime_llm=service,
        audio_buffer=IdentityFilter(),
        user_context_aggregator=user,
        assistant_context_aggregator=assistant,
        call_monitor_processor=monitor,
        agent_generation_processor=IdentityFilter(),
        pipeline_metrics_aggregator=IdentityFilter(),
        termination_funnel=IdentityFilter(),
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_out_sample_rate=16000),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    task = asyncio.create_task(run_worker_runner(create_worker_runner(), worker))
    try:
        await wait_for_pipeline_worker_started(worker, timeout=5, run_task=task)
        monitor.activate()
        await worker.queue_frame(UserStartedSpeakingFrame())
        if turn_timing == "before_response":
            await worker.queue_frame(UserStoppedSpeakingFrame())
        assert await worker.flush_pipeline(timeout=1)
        if turn_timing == "inference_first":
            await user.user_turn_controller._call_event_handler(
                "on_user_turn_inference_triggered", None, None
            )

        await service._push_user_transcription("Yes.")
        await service._handle_msg_model_turn(
            LiveServerMessage(
                server_content=LiveServerContent(
                    model_turn=Content(
                        parts=[
                            Part(
                                inline_data=Blob(
                                    data=b"\x01\x00" * 9600,
                                    mime_type="audio/pcm;rate=24000",
                                )
                            )
                        ]
                    )
                )
            )
        )
        if transcript_flush_delay < 1:
            await asyncio.wait_for(synced.wait(), 1)
        await service._handle_msg_turn_complete(
            LiveServerMessage(server_content=LiveServerContent(turn_complete=True))
        )
        await asyncio.wait_for(drained.wait(), 2)
        await asyncio.wait_for(synced.wait(), 1)
        if turn_timing != "before_response":
            # Local VAD can finalize after Gemini has already answered. This
            # must settle that turn, not demand another answer from Gemini.
            assert not idle.is_set()
            await worker.queue_frame(UserStoppedSpeakingFrame())
        await asyncio.wait_for(idle.wait(), 1)
        await asyncio.sleep(0.25)
        assert not expired.is_set()
        assert not monitor.pending_response
        assert context.messages[0]["content"] == "Yes."
    finally:
        playback.cancel_all()
        await worker.queue_frame(CancelFrame())
        await asyncio.wait_for(task, 3)
