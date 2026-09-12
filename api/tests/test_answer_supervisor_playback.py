"""Exercise all engine opening paths with the real aggregator and output sender."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    EndFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from api.schemas.answer_supervisor import resolve_answer_supervisor_config
from api.services.pipecat.pipeline_builder import build_pipeline
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor
from api.services.pipecat.run_pipeline import _create_user_mute_strategies
from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine
from pipecat.tests import MockLLMService, MockTTSService


class Passthrough(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
@pytest.mark.parametrize("screened", [False, True])
@pytest.mark.parametrize(
    "greeting_type, expected_generations", [("text", 0), ("audio", 0), ("llm", 1)]
)
async def test_hello_produces_exactly_one_opening(
    simple_workflow, greeting_type, expected_generations, screened
):
    node = simple_workflow.nodes[simple_workflow.start_node_id]
    node.greeting_type = greeting_type
    node.greeting = "Welcome." if greeting_type == "text" else None
    node.greeting_recording_id = "9" if greeting_type == "audio" else None
    context = LLMContext()
    llm = MockLLMService(
        mock_steps=[MockLLMService.create_text_chunks("Welcome.")], chunk_delay=0.001
    )
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)
    transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            audio_out_end_silence_secs=0,
        )
    )
    engine = PipecatEngine(
        llm=llm,
        workflow=simple_workflow,
        context=context,
        call_context_vars={},
        workflow_run_id=1,
    )
    engine.set_transport_output(transport.output())
    engine.set_fetch_recording_audio(
        AsyncMock(
            return_value=SimpleNamespace(
                audio=b"\0\0" * 640,
                transcript="Welcome.",
            )
        )
    )
    supervisor = AnswerSupervisor(
        resolve_answer_supervisor_config(
            {
                "enabled": True,
                "listening_window_ms": 500,
                "human_utterance_max_ms": 60,
                "screening_message": {"text": "Alex calling about your appointment."},
            },
            call_direction="outbound",
            is_realtime=False,
            start_node=node,
        ),
        context=context,
    )
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[ExternalUserTurnStartStrategy(enable_interruptions=False)],
                stop=[ExternalUserTurnStopStrategy()],
            ),
            user_mute_strategies=_create_user_mute_strategies(engine, supervisor),
        ),
    )
    supervisor.bind(aggregators.user())
    engine.set_answer_supervisor(supervisor, aggregators.user(), 10)
    pipeline = build_pipeline(
        transport=transport,
        stt=Passthrough(),
        audio_buffer=Passthrough(),
        llm=llm,
        tts=tts,
        user_context_aggregator=aggregators.user(),
        assistant_context_aggregator=aggregators.assistant(),
        pipeline_engine_callback_processor=Passthrough(),
        pipeline_metrics_aggregator=Passthrough(),
        termination_funnel=Passthrough(),
        answer_supervisor=supervisor,
    )
    worker = PipelineWorker(pipeline, enable_rtvi=False)
    engine.set_task(worker)
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        supervisor.arm()
        started.set()

    runner = asyncio.create_task(run_pipeline_worker(worker))
    action = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        action = asyncio.create_task(engine.handle_answer_supervision())
        if screened:
            await worker.queue_frame(VADUserStartedSpeakingFrame())
            await worker.queue_frame(ProposedUserStartedSpeakingFrame())
            await asyncio.sleep(0.09)
            await worker.queue_frame(
                TranscriptionFrame(
                    "Tell me your name and reason for calling.",
                    "synthetic",
                    "",
                    finalized=True,
                )
            )
            await worker.queue_frame(VADUserStoppedSpeakingFrame())
            await worker.queue_frame(ProposedUserStoppedSpeakingFrame())
            async with asyncio.timeout(2):
                while not supervisor._screening:
                    await asyncio.sleep(0.01)
        await worker.queue_frame(VADUserStartedSpeakingFrame())
        await worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await asyncio.sleep(0.02)
        await worker.queue_frame(
            TranscriptionFrame("Hello?", "synthetic", "", finalized=True)
        )
        await worker.queue_frame(VADUserStoppedSpeakingFrame())
        await worker.queue_frame(ProposedUserStoppedSpeakingFrame())
        await asyncio.wait_for(action, 3)
        assert llm.get_current_step() == expected_generations
        assert supervisor.llm_gate().dropped_contexts == (2 if screened else 1)
        assert [m["content"] for m in context.messages if m.get("role") == "user"] == [
            "Hello?"
        ]
        assert engine._speech_playback_finished.is_set()
        assert not supervisor.blocks_workflow
    finally:
        if action:
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)
        if not runner.done():
            await worker.queue_frame(EndFrame())
        await asyncio.wait_for(runner, 3)
