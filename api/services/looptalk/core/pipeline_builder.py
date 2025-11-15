"""Pipeline building logic for LoopTalk agents."""

from typing import Any, Dict

from loguru import logger

from api.db.db_client import DBClient
from api.services.looptalk.audio_streamer import get_or_create_audio_streamer
from api.services.looptalk.internal_transport import InternalTransport
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.pipeline_builder import (
    create_pipeline_components,
    create_pipeline_task,
)
from api.services.pipecat.pipeline_engine_callbacks_processor import (
    PipelineEngineCallbacksProcessor,
)
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
)
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.filters.stt_mute_filter import (
    STTMuteConfig,
    STTMuteFilter,
    STTMuteStrategy,
)


class LoopTalkPipelineBuilder:
    """Builds pipelines for LoopTalk agents."""

    def __init__(self, db_client: DBClient):
        """Initialize the pipeline builder.

        Args:
            db_client: Database client for fetching user configurations
        """
        self.db_client = db_client

    async def create_agent_pipeline(
        self,
        transport: InternalTransport,
        workflow: Any,
        test_session_id: int,
        agent_id: str,
        role: str,
    ) -> Dict[str, Any]:
        """Create a pipeline for an agent (actor or adversary).

        Args:
            transport: Internal transport for the agent
            workflow: Workflow model from database
            test_session_id: ID of the test session
            agent_id: Unique identifier for the agent
            role: Either "actor" or "adversary"

        Returns:
            Dictionary containing pipeline task, engine, and components
        """
        # Get user configuration from database
        user_config = await self.db_client.get_user_configurations(workflow.user_id)

        # Create pipeline components
        audio_config = AudioConfig(
            transport_in_sample_rate=16000,
            transport_out_sample_rate=16000,
            vad_sample_rate=16000,
            pipeline_sample_rate=16000,
        )

        # Create services
        stt = create_stt_service(user_config)
        llm = create_llm_service(user_config)
        tts = create_tts_service(user_config, audio_config)

        logger.debug(f"Created services for {role}: STT={stt}, LLM={llm}, TTS={tts}")

        audio_buffer, audio_synchronizer, transcript, context = (
            create_pipeline_components(audio_config)
        )

        context_aggregator = LLMContextAggregatorPair(context)

        # Get workflow graph
        workflow_graph = WorkflowGraph(
            ReactFlowDTO.model_validate(workflow.workflow_definition_with_fallback)
        )

        # Create engine
        engine = PipecatEngine(
            task=None,  # Will be set after creating the task
            llm=llm,
            context=context,
            tts=tts,
            workflow=workflow_graph,
            call_context_vars={},
            audio_buffer=audio_buffer,
            workflow_run_id=None,  # LoopTalk doesn't have workflow runs
        )

        # Create STT mute filter
        stt_mute_filter = STTMuteFilter(
            config=STTMuteConfig(
                strategies={STTMuteStrategy.FIRST_SPEECH},
            )
        )

        # Create pipeline engine callback processor
        pipeline_engine_callback_processor = PipelineEngineCallbacksProcessor(
            max_call_duration_seconds=300,
            max_duration_end_task_callback=engine.create_max_duration_callback(),
            generation_started_callback=engine.create_generation_started_callback(),
        )

        # Get aggregators
        user_context_aggregator = context_aggregator.user()
        assistant_context_aggregator = context_aggregator.assistant()

        # Register processors with synchronizer for merged audio
        audio_synchronizer.register_processors(
            audio_buffer.input(), audio_buffer.output()
        )

        # Get audio streamer for real-time streaming
        audio_streamer = get_or_create_audio_streamer(str(test_session_id), role)

        # Create pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                audio_buffer.input(),  # Record input audio
                audio_streamer,  # Stream audio to connected clients
                stt_mute_filter,
                stt,
                transcript.user(),
                user_context_aggregator,
                llm,
                pipeline_engine_callback_processor,
                tts,
                transport.output(),
                audio_buffer.output(),  # Record output audio
                transcript.assistant(),
                assistant_context_aggregator,
            ]
        )

        # Create pipeline task with unique conversation ID for tracing
        conversation_id = f"{test_session_id}-{role}-{agent_id}"
        task = create_pipeline_task(pipeline, conversation_id, audio_config)

        # Set the task on the engine
        engine.task = task

        return {
            "task": task,
            "engine": engine,
            "audio_buffer": audio_buffer,
            "audio_synchronizer": audio_synchronizer,
            "transcript": transcript,
            "assistant_context_aggregator": assistant_context_aggregator,
            "audio_streamer": audio_streamer,
        }
