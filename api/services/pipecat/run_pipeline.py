import asyncio
from typing import Optional

from fastapi import HTTPException, WebSocket
from loguru import logger

from api.db import db_client
from api.db.models import WorkflowModel
from api.enums import WorkflowRunMode
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import AudioConfig, create_audio_config
from api.services.pipecat.event_handlers import (
    register_audio_data_handler,
    register_event_handlers,
    register_transcript_handlers,
)
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.pipeline_builder import (
    build_pipeline,
    create_pipeline_components,
    create_pipeline_task,
)
from api.services.pipecat.pipeline_engine_callbacks_processor import (
    PipelineEngineCallbacksProcessor,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
)
from api.services.pipecat.tracing_config import setup_pipeline_tracing
from api.services.pipecat.transport_setup import (
    create_cloudonix_transport,
    create_stasis_transport,
    create_twilio_transport,
    create_vobiz_transport,
    create_vonage_transport,
    create_webrtc_transport,
)
from api.services.pipecat.ws_sender_registry import get_ws_sender
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.extensions.voicemail.voicemail_detector import VoicemailDetector
from pipecat.pipeline.base_task import PipelineTaskParams
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    FunctionCallUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_start import (
    ExternalUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
    SpeechTimeoutUserTurnStopStrategy,
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.run_context import set_current_run_id
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
            ambient_noise_config = workflow.workflow_configurations[
                "ambient_noise_configuration"
            ]

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


async def run_pipeline_vobiz(
    websocket_client: WebSocket,
    stream_id: str,
    call_id: str,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
) -> None:
    """Run pipeline for Vobiz using Plivo-compatible WebSocket protocol."""
    logger.info(
        f"[run {workflow_run_id}] Starting Vobiz pipeline - "
        f"stream_id={stream_id}, call_id={call_id}, workflow_id={workflow_id}"
    )
    set_current_run_id(workflow_run_id)

    cost_info = {"call_id": call_id}
    await db_client.update_workflow_run(workflow_run_id, cost_info=cost_info)

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

    try:
        audio_config = create_audio_config(WorkflowRunMode.VOBIZ.value)
        logger.info(
            f"[run {workflow_run_id}] Vobiz audio config: "
            f"sample_rate={audio_config.transport_in_sample_rate}Hz, format=MULAW"
        )

        transport = await create_vobiz_transport(
            websocket_client,
            stream_id,
            call_id,
            workflow_run_id,
            audio_config,
            workflow.organization_id,
            vad_config,
            ambient_noise_config,
        )

        logger.info(f"[run {workflow_run_id}] Starting Vobiz pipeline execution")
        await _run_pipeline(
            transport,
            workflow_id,
            workflow_run_id,
            user_id,
            audio_config=audio_config,
        )
        logger.info(f"[run {workflow_run_id}] Vobiz pipeline completed successfully")

    except Exception as e:
        logger.error(
            f"[run {workflow_run_id}] Error in Vobiz pipeline: {e}", exc_info=True
        )
        raise


async def run_pipeline_cloudonix(
    websocket_client: WebSocket,
    stream_sid: str,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
) -> None:
    """Run pipeline for Cloudonix connections"""
    logger.debug(
        f"Running pipeline for Cloudonix connection with workflow_id: {workflow_id} and workflow_run_id: {workflow_run_id}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    call_id = workflow_run.gathered_context.get("call_id")
    if not call_id:
        logger.warning("call_id not found in gathered_context")
        raise Exception()

    # Store call ID in cost_info for later cost calculation (provider-agnostic)
    cost_info = {"call_id": call_id}
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

    # Create audio configuration for Cloudonix
    audio_config = create_audio_config(WorkflowRunMode.CLOUDONIX.value)

    transport = await create_cloudonix_transport(
        websocket_client,
        call_id,
        stream_sid,
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

    # Get workflow first so we can extract configurations before creating services
    workflow = await db_client.get_workflow(workflow_id, user_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Extract configurations from workflow configurations
    max_call_duration_seconds = 300  # Default 5 minutes
    max_user_idle_timeout = 10.0  # Default 10 seconds
    smart_turn_stop_secs = 2.0  # Default 2 seconds for incomplete turn timeout
    turn_stop_strategy = "transcription"  # Default to transcription-based detection
    keyterms = None  # Dictionary words for STT boosting

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

        # Use workflow-specific smart turn stop timeout if provided
        if "smart_turn_stop_secs" in workflow.workflow_configurations:
            smart_turn_stop_secs = workflow.workflow_configurations[
                "smart_turn_stop_secs"
            ]

        # Use workflow-specific turn stop strategy if provided
        if "turn_stop_strategy" in workflow.workflow_configurations:
            turn_stop_strategy = workflow.workflow_configurations["turn_stop_strategy"]

        # Extract dictionary words and convert to keyterms list
        if "dictionary" in workflow.workflow_configurations:
            dictionary = workflow.workflow_configurations["dictionary"]
            if dictionary and isinstance(dictionary, str):
                # Split by comma and strip whitespace from each term
                keyterms = [
                    term.strip() for term in dictionary.split(",") if term.strip()
                ]

    # Create services based on user configuration
    stt = create_stt_service(user_config, audio_config, keyterms=keyterms)
    tts = create_tts_service(user_config, audio_config)
    llm = create_llm_service(user_config)

    workflow_graph = WorkflowGraph(
        ReactFlowDTO.model_validate(workflow.workflow_definition_with_fallback)
    )

    # Create in-memory logs buffer early so it can be used by engine callbacks
    in_memory_logs_buffer = InMemoryLogsBuffer(workflow_run_id)

    # Create node transition callback if WebSocket sender is available
    node_transition_callback = None
    ws_sender = get_ws_sender(workflow_run_id)
    if ws_sender:

        async def send_node_transition(
            node_name: str, previous_node: Optional[str]
        ) -> None:
            """Send node transition event via WebSocket AND log to buffer."""
            message = {
                "type": "rtf-node-transition",
                "payload": {
                    "node_name": node_name,
                    "previous_node": previous_node,
                },
            }
            # Send via WebSocket
            try:
                await ws_sender(message)
            except Exception as e:
                logger.debug(f"Failed to send node transition via WebSocket: {e}")

            # Log to in-memory buffer
            try:
                await in_memory_logs_buffer.append(message)
            except Exception as e:
                logger.error(f"Failed to append node transition to logs buffer: {e}")

        node_transition_callback = send_node_transition

    # Extract embeddings configuration from user config
    embeddings_api_key = None
    embeddings_model = None
    embeddings_base_url = None
    if user_config and user_config.embeddings:
        embeddings_api_key = user_config.embeddings.api_key
        embeddings_model = user_config.embeddings.model
        embeddings_base_url = getattr(user_config.embeddings, "base_url", None)

    engine = PipecatEngine(
        llm=llm,
        workflow=workflow_graph,
        call_context_vars=merged_call_context_vars,
        workflow_run_id=workflow_run_id,
        node_transition_callback=node_transition_callback,
        embeddings_api_key=embeddings_api_key,
        embeddings_model=embeddings_model,
        embeddings_base_url=embeddings_base_url,
    )

    # Create pipeline components
    audio_buffer, context = create_pipeline_components(audio_config)

    # Set the context, audio_config, and audio_buffer after creation
    engine.set_context(context)
    engine.set_audio_config(audio_config)

    # Set Stasis connection for immediate transfers (if available)
    if stasis_connection:
        engine.set_stasis_connection(stasis_connection)

    assistant_params = LLMAssistantAggregatorParams(
        expect_stripped_words=True,
        correct_aggregation_callback=engine.create_aggregation_correction_callback(),
    )

    # Configure turn strategies based on STT provider, model, and workflow configuration
    # Deepgram Flux uses external turn detection (VAD + External start/stop)
    # Other models use configurable turn detection strategy
    is_deepgram_flux = (
        user_config.stt.provider == ServiceProviders.DEEPGRAM.value
        and user_config.stt.model == "flux-general-en"
    )

    if is_deepgram_flux:
        user_turn_strategies = UserTurnStrategies(
            start=[
                VADUserTurnStartStrategy(),
                ExternalUserTurnStartStrategy(enable_interruptions=True),
            ],
            stop=[ExternalUserTurnStopStrategy()],
        )
    elif turn_stop_strategy == "turn_analyzer":
        # Smart Turn Analyzer: best for longer responses with natural pauses
        smart_turn_params = SmartTurnParams(stop_secs=smart_turn_stop_secs)
        user_turn_strategies = UserTurnStrategies(
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            stop=[
                TurnAnalyzerUserTurnStopStrategy(
                    turn_analyzer=LocalSmartTurnAnalyzerV3(params=smart_turn_params)
                )
            ],
        )
    else:
        # Transcription-based (default): best for short 1-2 word responses
        user_turn_strategies = UserTurnStrategies(
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy()],
        )

    # Create user mute strategies
    # - CallbackUserMuteStrategy: mutes based on engine's _mute_pipeline state
    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        FunctionCallUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_turn_strategies=user_turn_strategies,
        user_mute_strategies=user_mute_strategies,
        user_idle_timeout=max_user_idle_timeout,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.3)),
    )
    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )

    # Create usage metrics aggregator with engine's callback
    pipeline_engine_callback_processor = PipelineEngineCallbacksProcessor(
        max_call_duration_seconds=max_call_duration_seconds,
        max_duration_end_task_callback=engine.create_max_duration_callback(),
        generation_started_callback=engine.create_generation_started_callback(),
        llm_text_frame_callback=engine.handle_llm_text_frame,
    )

    pipeline_metrics_aggregator = PipelineMetricsAggregator()

    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Register user idle event handlers
    user_idle_handler = engine.create_user_idle_handler()

    @user_context_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await user_idle_handler.handle_idle(aggregator)

    @user_context_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        user_idle_handler.reset()

    # Create voicemail detector if enabled in the workflow's start node
    voicemail_detector = None
    start_node = workflow_graph.nodes.get(workflow_graph.start_node_id)
    if start_node and start_node.detect_voicemail:
        logger.info(f"Voicemail detection enabled for workflow run {workflow_run_id}")
        # Create a separate LLM instance for the voicemail sub-pipeline
        # (can't share with main pipeline as it would mess up frame linking)
        voicemail_llm = create_llm_service(user_config)
        voicemail_detector = VoicemailDetector(
            llm=voicemail_llm,
            voicemail_response_delay=2.0,
        )

        # Register event handler to end task when voicemail is detected
        @voicemail_detector.event_handler("on_voicemail_detected")
        async def _on_voicemail_detected(_processor):
            logger.info(f"Voicemail detected for workflow run {workflow_run_id}")
            await engine.end_call_with_reason(
                reason=EndTaskReason.VOICEMAIL_DETECTED.value,
                abort_immediately=True,
            )

    # Build the pipeline with the STT mute filter and context controller
    pipeline = build_pipeline(
        transport,
        stt,
        audio_buffer,
        llm,
        tts,
        user_context_aggregator,
        assistant_context_aggregator,
        pipeline_engine_callback_processor,
        pipeline_metrics_aggregator,
        voicemail_detector=voicemail_detector,
    )

    # Create pipeline task with audio configuration
    task = create_pipeline_task(pipeline, workflow_run_id, audio_config)

    # Now set the task on the engine
    engine.set_task(task)

    # Initialize the engine to set the initial context
    await engine.initialize()

    # Add real-time feedback observer if WebSocket sender is available
    # Note: ws_sender was already fetched earlier for node_transition_callback
    if ws_sender:
        feedback_observer = RealtimeFeedbackObserver(
            ws_sender=ws_sender,
            logs_buffer=in_memory_logs_buffer,
        )
        task.add_observer(feedback_observer)

    # Register event handlers
    in_memory_audio_buffer, in_memory_transcript_buffer = register_event_handlers(
        task,
        transport,
        workflow_run_id,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=in_memory_logs_buffer,
        pipeline_metrics_aggregator=pipeline_metrics_aggregator,
        audio_config=audio_config,
    )

    register_audio_data_handler(audio_buffer, workflow_run_id, in_memory_audio_buffer)
    register_transcript_handlers(
        user_context_aggregator,
        assistant_context_aggregator,
        workflow_run_id,
        in_memory_transcript_buffer,
    )

    try:
        # Run the pipeline
        loop = asyncio.get_running_loop()
        params = PipelineTaskParams(loop=loop)
        await task.run(params)
        logger.info(f"Task completed for run {workflow_run_id}")
    except asyncio.CancelledError:
        logger.warning("Received CancelledError in _run_pipeline")
    finally:
        ContextProviderRegistry.remove_providers(str(workflow_run_id))
        logger.debug(f"Cleaned up context providers for workflow run {workflow_run_id}")
