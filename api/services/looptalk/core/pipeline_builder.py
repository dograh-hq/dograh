"""Pipeline building logic for LoopTalk agents."""

from typing import Any, Dict

from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)

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

        # Use published definition for graph + configs
        released_def = workflow.released_definition
        wf_json = released_def.workflow_json
        wf_configs = released_def.workflow_configurations or {}

        # Extract keyterms from workflow configurations
        keyterms = None
        if wf_configs and "dictionary" in wf_configs:
            dictionary = wf_configs["dictionary"]
            if dictionary and isinstance(dictionary, str):
                keyterms = [
                    term.strip() for term in dictionary.split(",") if term.strip()
                ]
                if keyterms:
                    logger.info(f"Using {len(keyterms)} keyterms for STT: {keyterms}")

        # Resolve model overrides from the version onto global user config
        from api.services.configuration.resolve import resolve_effective_config

        model_overrides = wf_configs.get("model_overrides")
        user_config = resolve_effective_config(user_config, model_overrides)

        # Create services
        stt = create_stt_service(user_config, audio_config, keyterms=keyterms)
        llm = create_llm_service(user_config)
        tts = create_tts_service(user_config, audio_config)

        logger.debug(f"Created services for {role}: STT={stt}, LLM={llm}, TTS={tts}")

        # Get workflow graph
        workflow_graph = WorkflowGraph(ReactFlowDTO.model_validate(wf_json))

        # Create engine first (needed for create_pipeline_components)
        engine = PipecatEngine(
            llm=llm,
            workflow=workflow_graph,
            call_context_vars={},
            workflow_run_id=None,  # LoopTalk doesn't have workflow runs
        )

        # Create pipeline components with audio configuration and engine
        audio_buffer, transcript, context = create_pipeline_components(
            audio_config, engine
        )

        # Set the context and audio_buffer after creation
        engine.set_context(context)

        context_aggregator = LLMContextAggregatorPair(context)

        # Create pipeline engine callback processor
        pipeline_engine_callback_processor = PipelineEngineCallbacksProcessor(
            max_call_duration_seconds=300,
            max_duration_end_task_callback=engine.create_max_duration_callback(),
            generation_started_callback=engine.create_generation_started_callback(),
        )

        # Get aggregators
        user_context_aggregator = context_aggregator.user()
        assistant_context_aggregator = context_aggregator.assistant()

        # Get audio streamer for real-time streaming
        audio_streamer = get_or_create_audio_streamer(str(test_session_id), role)

        # Create pipeline with AudioBufferProcessor after transport.output()
        pipeline = Pipeline(
            [
                transport.input(),
                audio_streamer,  # Stream audio to connected clients
                stt,
                transcript.user(),
                user_context_aggregator,
                llm,
                pipeline_engine_callback_processor,
                tts,
                transport.output(),
                audio_buffer,  # AudioBufferProcessor - records both input and output audio
                transcript.assistant(),
                assistant_context_aggregator,
            ]
        )

        # Create pipeline task with unique conversation ID for tracing
        conversation_id = f"{test_session_id}-{role}-{agent_id}"
        task = create_pipeline_task(pipeline, conversation_id, audio_config)

        # Set the task on the engine
        engine.set_task(task)

        return {
            "task": task,
            "engine": engine,
            "audio_buffer": audio_buffer,
            "transcript": transcript,
            "assistant_context_aggregator": assistant_context_aggregator,
            "audio_streamer": audio_streamer,
        }
