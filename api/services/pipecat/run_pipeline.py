from typing import Optional

from fastapi import HTTPException, WebSocket
from loguru import logger

from api.db import db_client
from api.db.models import WorkflowModel
from api.enums import WorkflowRunMode
from api.services.pipecat.audio_config import AudioConfig, create_audio_config
from api.services.pipecat.engine_pre_aggregator_processor import (
    EnginePreAggregatorProcessor,
)
from api.services.pipecat.event_handlers import (
    register_audio_data_handler,
    register_task_event_handler,
    register_transcript_handler,
    register_transport_event_handlers,
)
from api.services.pipecat.pipeline_builder import (
    build_pipeline,
    create_pipeline_components,
    create_pipeline_task,
)
from api.services.pipecat.pipeline_engine_callbacks_processor import (
    PipelineEngineCallbacksProcessor,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
)
from api.services.pipecat.tracing_config import setup_pipeline_tracing
from api.services.pipecat.transport_setup import (
    create_stasis_transport,
    create_twilio_transport,
    create_vonage_transport,
    create_webrtc_transport,
)
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.aggregators.llm_response import LLMAssistantAggregatorParams
from pipecat.processors.filters.stt_mute_filter import (
    STTMuteConfig,
    STTMuteFilter,
    STTMuteStrategy,
)
from pipecat.processors.user_idle_processor import UserIdleProcessor
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.utils.context import set_current_run_id
from pipecat.utils.tracing.context_registry import ContextProviderRegistry

# Setup tracing if enabled
setup_pipeline_tracing()


async def run_pipeline_twilio(
    websocket_client: WebSocket,
    stream_sid: str,
    call_sid: str,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
) -> None:
    """Run pipeline for Twilio connections"""
    logger.debug(
        f"Running pipeline for Twilio connection with workflow_id: {workflow_id} and workflow_run_id: {workflow_run_id}"
    )
    set_current_run_id(workflow_run_id)

    # Store call ID in cost_info for later cost calculation (provider-agnostic)
    cost_info = {"call_id": call_sid}
    await db_client.update_workflow_run(workflow_run_id, cost_info=cost_info)

    # Get workflow to extract all pipeline configurations
    workflow = await db_client.get_workflow(workflow_id, user_id)
    vad_config = None
    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        if "vad_configuration" in workflow.workflow_configurations:
            vad_config = workflow.workflow_configurations["vad_configuration"]
        if "ambient_noise_configuration" in workflow.workflow_configurations:
            ambient_noise_config = workflow.workflow_configurations[
                "ambient_noise_configuration"
            ]

    # Create audio configuration for Twilio
    audio_config = create_audio_config(WorkflowRunMode.TWILIO.value)

    transport = await create_twilio_transport(
        websocket_client,
        stream_sid,
        call_sid,
        workflow_run_id,
        audio_config,
        workflow.organization_id,
        vad_config,
        ambient_noise_config,
    )
    await _run_pipeline(
        transport,
        workflow_id,
        workflow_run_id,
        user_id,
        audio_config=audio_config,
    )


async def run_pipeline_vonage(
    websocket_client,
    call_uuid: str,
    workflow: WorkflowModel,
    organization_id: int,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
):
    """Run pipeline for Vonage WebSocket connections.
    
    Vonage uses raw PCM audio over WebSocket instead of base64-encoded μ-law.
    The audio is transmitted as binary frames at 16kHz by default.
    """
    logger.info(f"Starting Vonage pipeline for workflow run {workflow_run_id}")
    set_current_run_id(workflow_run_id)

    # Store call ID in cost_info for later cost calculation (provider-agnostic)
    cost_info = {"call_id": call_uuid}
    await db_client.update_workflow_run(workflow_run_id, cost_info=cost_info)

    # Extract VAD and ambient noise config from workflow
    vad_config = None
    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        if "vad_configuration" in workflow.workflow_configurations:
            vad_config = workflow.workflow_configurations["vad_configuration"]
        if "ambient_noise_configuration" in workflow.workflow_configurations:
            ambient_noise_config = workflow.workflow_configurations["ambient_noise_configuration"]

    try:
        # Setup audio config for Vonage using the centralized config
        audio_config = create_audio_config(WorkflowRunMode.VONAGE.value)

        # Create Vonage transport
        transport = await create_vonage_transport(
            websocket_client,
            call_uuid,
            workflow_run_id,
            audio_config,
            organization_id,
            vad_config,
            ambient_noise_config,
        )

        # No special handshake needed for Vonage
        # Audio streaming starts immediately

        # Run the pipeline (same as Twilio/WebRTC)
        await _run_pipeline(
            transport,
            workflow_id,
            workflow_run_id,
            user_id,
            call_context_vars={},
            audio_config=audio_config,
        )

    except Exception as e:
        logger.error(f"Error in Vonage pipeline: {e}")
        raise


async def run_pipeline_smallwebrtc(
    webrtc_connection: SmallWebRTCConnection,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_context_vars: dict = {},
) -> None:
    """Run pipeline for WebRTC connections"""
    logger.debug(
        f"Running pipeline for WebRTC connection with workflow_id: {workflow_id} and workflow_run_id: {workflow_run_id}"
    )
    set_current_run_id(workflow_run_id)

    # Get workflow to extract all pipeline configurations
    workflow = await db_client.get_workflow(workflow_id, user_id)
    vad_config = None
    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        if "vad_configuration" in workflow.workflow_configurations:
            vad_config = workflow.workflow_configurations["vad_configuration"]
        if "ambient_noise_configuration" in workflow.workflow_configurations:
            ambient_noise_config = workflow.workflow_configurations[
                "ambient_noise_configuration"
            ]

    # Create audio configuration for WebRTC
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)

    transport = create_webrtc_transport(
        webrtc_connection,
        workflow_run_id,
        audio_config,
        vad_config,
        ambient_noise_config,
    )
    await _run_pipeline(
        transport,
        workflow_id,
        workflow_run_id,
        user_id,
        call_context_vars=call_context_vars,
        audio_config=audio_config,
    )


async def run_pipeline_ari_stasis(
    stasis_connection: StasisRTPConnection,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_context_vars: dict,
) -> None:
    """Run pipeline for ARI connections"""
    logger.debug(
        f"Running pipeline for ARI connection with workflow_id: {workflow_id} and workflow_run_id: {workflow_run_id}"
    )
    set_current_run_id(workflow_run_id)

    # Get workflow to extract all pipeline configurations
    workflow = await db_client.get_workflow(workflow_id, user_id)
    vad_config = None
    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        if "vad_configuration" in workflow.workflow_configurations:
            vad_config = workflow.workflow_configurations["vad_configuration"]
        if "ambient_noise_configuration" in workflow.workflow_configurations:
            ambient_noise_config = workflow.workflow_configurations[
                "ambient_noise_configuration"
            ]

    # Create audio configuration for Stasis
    audio_config = create_audio_config(WorkflowRunMode.STASIS.value)

    transport = create_stasis_transport(
        stasis_connection,
        workflow_run_id,
        audio_config,
        vad_config,
        ambient_noise_config,
    )
    await _run_pipeline(
        transport,
        workflow_id,
        workflow_run_id,
        user_id,
        call_context_vars=call_context_vars,
        audio_config=audio_config,
        stasis_connection=stasis_connection,  # Pass connection for immediate transfers
    )


async def _run_pipeline(
    transport,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_context_vars: dict = {},
    audio_config: AudioConfig = None,
    stasis_connection: Optional[StasisRTPConnection] = None,
) -> None:
    """
    Run the pipeline with the given transport and configuration

    Args:
        transport: The transport to use for the pipeline
        workflow_id: The ID of the workflow
        workflow_run_id: The ID of the workflow run
        user_id: The ID of the user
        mode: The mode of the pipeline (twilio or smallwebrtc)
    """
    workflow_run = await db_client.get_workflow_run(workflow_run_id, user_id)

    # If the workflow run is already completed, we don't need to run it again
    if workflow_run.is_completed:
        raise HTTPException(status_code=400, detail="Workflow run already completed")

    merged_call_context_vars = workflow_run.initial_context
    # If there is some extra call_context_vars, update them
    if call_context_vars:
        merged_call_context_vars = {**merged_call_context_vars, **call_context_vars}
        await db_client.update_workflow_run(
            workflow_run_id, initial_context=merged_call_context_vars
        )

    # Get user configuration
    user_config = await db_client.get_user_configurations(user_id)

    # Create services based on user configuration
    stt = create_stt_service(user_config)
    tts = create_tts_service(user_config, audio_config)
    llm = create_llm_service(user_config)

    # Get workflow first so we can create engine before pipeline components
    workflow = await db_client.get_workflow(workflow_id, user_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Extract configurations from workflow configurations
    max_call_duration_seconds = 300  # Default 5 minutes
    max_user_idle_timeout = 10.0  # Default 10 seconds

    if workflow.workflow_configurations:
        # Use workflow-specific max call duration if provided
        if "max_call_duration" in workflow.workflow_configurations:
            max_call_duration_seconds = workflow.workflow_configurations[
                "max_call_duration"
            ]

        # Use workflow-specific max user idle timeout if provided
        if "max_user_idle_timeout" in workflow.workflow_configurations:
            max_user_idle_timeout = workflow.workflow_configurations[
                "max_user_idle_timeout"
            ]

    workflow_graph = WorkflowGraph(
        ReactFlowDTO.model_validate(workflow.workflow_definition_with_fallback)
    )

    engine = PipecatEngine(
        llm=llm,
        tts=tts,
        workflow=workflow_graph,
        call_context_vars=merged_call_context_vars,
        workflow_run_id=workflow_run_id,
    )

    # Create pipeline components with audio configuration and engine
    audio_buffer, audio_synchronizer, transcript, context = create_pipeline_components(
        audio_config, engine
    )

    # Set the context and audio_buffer after creation
    engine.set_context(context)
    engine.set_audio_buffer(audio_buffer)

    # Set Stasis connection for immediate transfers (if available)
    if stasis_connection:
        engine.set_stasis_connection(stasis_connection)

    assistant_params = LLMAssistantAggregatorParams(
        expect_stripped_words=True,
        correct_aggregation_callback=engine.create_aggregation_correction_callback(),
    )
    context_aggregator = llm.create_context_aggregator(
        context, assistant_params=assistant_params
    )

    # Create engine pre-aggregator processor for speaking events
    engine_pre_aggregator_processor = EnginePreAggregatorProcessor(
        user_started_speaking_callback=engine.create_user_started_speaking_callback(),
        user_stopped_speaking_callback=engine.create_user_stopped_speaking_callback(),
    )

    # Create usage metrics aggregator with engine's callback
    pipeline_engine_callback_processor = PipelineEngineCallbacksProcessor(
        max_call_duration_seconds=max_call_duration_seconds,
        max_duration_end_task_callback=engine.create_max_duration_callback(),
        llm_generated_text_callback=engine.create_llm_generated_text_callback(),
        generation_started_callback=engine.create_generation_started_callback(),
        llm_text_frame_callback=engine.handle_llm_text_frame,
        # Note: speaking event callbacks are now handled by pre-aggregator processor
    )

    pipeline_metrics_aggregator = PipelineMetricsAggregator()

    # Create STT mute filter using the selected strategies and the engine's callback
    stt_mute_filter = STTMuteFilter(
        config=STTMuteConfig(
            strategies={
                STTMuteStrategy.MUTE_UNTIL_FIRST_BOT_COMPLETE,
                STTMuteStrategy.CUSTOM,
            },
            should_mute_callback=engine.create_should_mute_callback(),
        )
    )

    # Use engine's user idle callback with configured timeout
    user_idle_disconnect = UserIdleProcessor(
        callback=engine.create_user_idle_callback(), timeout=max_user_idle_timeout
    )

    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    @assistant_context_aggregator.event_handler("on_push_aggregation")
    async def on_assistant_aggregator_push_context(_aggregator):
        logger.debug("Assistant aggregator push context – flushing pending transitions")
        await engine.flush_pending_transitions(source="context_push")

    # Build the pipeline with the STT mute filter and context controller
    pipeline = build_pipeline(
        transport,
        stt,
        transcript,
        audio_buffer,
        audio_synchronizer,
        llm,
        tts,
        user_context_aggregator,
        assistant_context_aggregator,
        pipeline_engine_callback_processor,
        stt_mute_filter,
        pipeline_metrics_aggregator,
        user_idle_disconnect,
        engine_pre_aggregator_processor=engine_pre_aggregator_processor,
    )

    # Create pipeline task with audio configuration
    task = create_pipeline_task(pipeline, workflow_run_id, audio_config)

    # Now set the task on the engine
    engine.set_task(task)

    # Register event handlers
    in_memory_audio_buffer, in_memory_transcript_buffer = (
        register_transport_event_handlers(
            task,
            transport,
            workflow_run_id,
            engine=engine,
            audio_buffer=audio_buffer,
            audio_synchronizer=audio_synchronizer,
            audio_config=audio_config,
        )
    )

    register_task_event_handler(
        workflow_run_id,
        engine,
        task,
        transport,
        audio_buffer,
        audio_synchronizer,
        in_memory_audio_buffer,
        in_memory_transcript_buffer,
        pipeline_metrics_aggregator,
    )

    register_audio_data_handler(
        audio_synchronizer, workflow_run_id, in_memory_audio_buffer
    )
    register_transcript_handler(
        transcript, workflow_run_id, in_memory_transcript_buffer
    )

    try:
        # Run the pipeline
        runner = PipelineRunner()
        await runner.run(task)
        logger.info(f"Pipeline runner completed for run {workflow_run_id}")
    finally:
        ContextProviderRegistry.remove_providers(str(workflow_run_id))
        logger.debug(f"Cleaned up context providers for workflow run {workflow_run_id}")
