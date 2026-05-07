import asyncio
from typing import Optional

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import AudioConfig, create_audio_config
from api.services.pipecat.event_handlers import (
    register_audio_data_handler,
    register_event_handlers,
)
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.pipeline_builder import (
    build_pipeline,
    build_realtime_pipeline,
    create_pipeline_components,
    create_pipeline_task,
)
from api.services.pipecat.pipeline_engine_callbacks_processor import (
    PipelineEngineCallbacksProcessor,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.pre_call_fetch import execute_pre_call_fetch
from api.services.pipecat.realtime_feedback_observer import (
    RealtimeFeedbackObserver,
    register_turn_log_handlers,
)
from api.services.pipecat.recording_audio_cache import (
    create_recording_audio_fetcher,
    warm_recording_cache,
)
from api.services.pipecat.recording_router_processor import RecordingRouterProcessor
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_llm_service_from_provider,
    create_realtime_llm_service,
    create_stt_service,
    create_tts_service,
)
from api.services.pipecat.tracing_config import (
    ensure_tracing,
)
from api.services.pipecat.transport_setup import create_webrtc_transport
from api.services.pipecat.ws_sender_registry import get_ws_sender
from api.services.telephony import registry as telephony_registry
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
from pipecat.utils.enums import EndTaskReason, RealtimeFeedbackType
from pipecat.utils.run_context import set_current_org_id, set_current_run_id

# Setup tracing if enabled
ensure_tracing()


async def run_pipeline_telephony(
    websocket,
    *,
    provider_name: str,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_id: str,
    transport_kwargs: dict,
) -> None:
    """Run a pipeline for any telephony provider.

    Replaces the previous per-provider run_pipeline_<x> functions. The
    provider's transport factory and audio config are looked up from the
    registry, so adding a new provider requires no changes here.

    Args:
        websocket: The accepted WebSocket from the provider.
        provider_name: Stable identifier of the provider (registry key).
        workflow_id: Workflow being executed.
        workflow_run_id: Workflow run row.
        user_id: Owner of the workflow.
        call_id: Provider call identifier (stored in cost_info for billing).
        transport_kwargs: Provider-specific kwargs forwarded to the transport
            factory (e.g. stream_sid + call_sid for Twilio).
    """
    logger.debug(f"Running {provider_name} pipeline for workflow_run {workflow_run_id}")
    set_current_run_id(workflow_run_id)

    await db_client.update_workflow_run(workflow_run_id, cost_info={"call_id": call_id})

    workflow = await db_client.get_workflow(workflow_id, user_id)
    if workflow:
        set_current_org_id(workflow.organization_id)

    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        ambient_noise_config = workflow.workflow_configurations.get(
            "ambient_noise_configuration"
        )

    # The telephony config id is stamped on the workflow run when it's created
    # (test call, campaign dispatch, inbound). Transports use it to load creds
    # from the right config row. Falls back to None for legacy runs (transports
    # then resolve the org's default config).
    workflow_run = await db_client.get_workflow_run(workflow_run_id)
    telephony_configuration_id = None
    if workflow_run and workflow_run.initial_context:
        telephony_configuration_id = workflow_run.initial_context.get(
            "telephony_configuration_id"
        )

    spec = telephony_registry.get(provider_name)
    audio_config = create_audio_config(provider_name)

    transport = await spec.transport_factory(
        websocket,
        workflow_run_id,
        audio_config,
        workflow.organization_id,
        ambient_noise_config=ambient_noise_config,
        telephony_configuration_id=telephony_configuration_id,
        **transport_kwargs,
    )

    try:
        await _run_pipeline(
            transport,
            workflow_id,
            workflow_run_id,
            user_id,
            audio_config=audio_config,
        )
    except Exception as e:
        logger.error(
            f"[run {workflow_run_id}] Error in {provider_name} pipeline: {e}",
            exc_info=True,
        )
        raise


async def run_pipeline_smallwebrtc(
    webrtc_connection: SmallWebRTCConnection,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_context_vars: dict = {},
    user_provider_id: str | None = None,
) -> None:
    """Run pipeline for WebRTC connections"""
    logger.debug(
        f"Running pipeline for WebRTC connection with workflow_id: {workflow_id} and workflow_run_id: {workflow_run_id}"
    )
    set_current_run_id(workflow_run_id)

    # Get workflow to extract all pipeline configurations
    workflow = await db_client.get_workflow(workflow_id, user_id)

    # Set org context early so tasks created by the transport inherit it
    if workflow:
        set_current_org_id(workflow.organization_id)

    ambient_noise_config = None
    if workflow and workflow.workflow_configurations:
        if "ambient_noise_configuration" in workflow.workflow_configurations:
            ambient_noise_config = workflow.workflow_configurations[
                "ambient_noise_configuration"
            ]

    # Create audio configuration for WebRTC
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)

    transport = await create_webrtc_transport(
        webrtc_connection,
        workflow_run_id,
        audio_config,
        ambient_noise_config,
    )
    await _run_pipeline(
        transport,
        workflow_id,
        workflow_run_id,
        user_id,
        call_context_vars=call_context_vars,
        audio_config=audio_config,
        user_provider_id=user_provider_id,
    )


async def _run_pipeline(
    transport,
    workflow_id: int,
    workflow_run_id: int,
    user_id: int,
    call_context_vars: dict = {},
    audio_config: AudioConfig = None,
    user_provider_id: str | None = None,
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

    # Get workflow for metadata (name, organization_id, call_disposition_codes)
    workflow = await db_client.get_workflow(workflow_id, user_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Use the run's pinned definition for graph + configs (not the workflow's current)
    run_definition = workflow_run.definition
    run_workflow_json = run_definition.workflow_json
    run_configs = run_definition.workflow_configurations or {}

    # Extract configurations from the version's workflow_configurations
    max_call_duration_seconds = 300  # Default 5 minutes
    max_user_idle_timeout = 10.0  # Default 10 seconds
    smart_turn_stop_secs = 2.0  # Default 2 seconds for incomplete turn timeout
    turn_stop_strategy = "transcription"  # Default to transcription-based detection
    keyterms = None  # Dictionary words for STT boosting

    if run_configs:
        if "max_call_duration" in run_configs:
            max_call_duration_seconds = run_configs["max_call_duration"]

        if "max_user_idle_timeout" in run_configs:
            max_user_idle_timeout = run_configs["max_user_idle_timeout"]

        if "smart_turn_stop_secs" in run_configs:
            smart_turn_stop_secs = run_configs["smart_turn_stop_secs"]

        if "turn_stop_strategy" in run_configs:
            turn_stop_strategy = run_configs["turn_stop_strategy"]

        if "dictionary" in run_configs:
            dictionary = run_configs["dictionary"]
            if dictionary and isinstance(dictionary, str):
                keyterms = [
                    term.strip() for term in dictionary.split(",") if term.strip()
                ]

    # Resolve model overrides from the version onto global user config
    from api.services.configuration.resolve import resolve_effective_config

    model_overrides = run_configs.get("model_overrides")
    user_config = resolve_effective_config(user_config, model_overrides)

    # Detect realtime mode (speech-to-speech services like OpenAI Realtime, Gemini Live)
    is_realtime = user_config.is_realtime and user_config.realtime is not None

    # Create services based on user configuration
    if is_realtime:
        llm = create_realtime_llm_service(user_config, audio_config)
        stt = None
        tts = None
        # Realtime services don't implement run_inference, so create a
        # separate text LLM for variable extraction and other out-of-band
        # inference calls.
        inference_llm = create_llm_service(user_config)
    else:
        stt = create_stt_service(user_config, audio_config, keyterms=keyterms)
        tts = create_tts_service(user_config, audio_config)
        llm = create_llm_service(user_config)
        inference_llm = None

    workflow_graph = WorkflowGraph(ReactFlowDTO.model_validate(run_workflow_json))

    # Pre-call fetch: fire early so it runs concurrently with remaining setup
    pre_call_fetch_task = None
    start_node = workflow_graph.nodes.get(workflow_graph.start_node_id)
    if (
        start_node
        and start_node.pre_call_fetch_enabled
        and start_node.pre_call_fetch_url
    ):
        logger.info(
            f"Pre-call fetch enabled for workflow run {workflow_run_id}, "
            f"firing request to {start_node.pre_call_fetch_url}"
        )
        pre_call_fetch_task = asyncio.create_task(
            execute_pre_call_fetch(
                url=start_node.pre_call_fetch_url,
                credential_uuid=start_node.pre_call_fetch_credential_uuid,
                call_context_vars=merged_call_context_vars,
                workflow_id=workflow_id,
                organization_id=workflow.organization_id,
            )
        )

    # Create in-memory logs buffer early so it can be used by engine callbacks
    in_memory_logs_buffer = InMemoryLogsBuffer(workflow_run_id)

    # Create node transition callback (always logs to buffer, optionally streams to WS)
    ws_sender = get_ws_sender(workflow_run_id)

    async def send_node_transition(
        node_id: str,
        node_name: str,
        previous_node_id: Optional[str],
        previous_node_name: Optional[str],
        allow_interrupt: bool = False,
    ) -> None:
        """Send node transition event to logs buffer and optionally via WebSocket."""
        # Update current node on the buffer so subsequent events are tagged
        in_memory_logs_buffer.set_current_node(node_id, node_name)

        message = {
            "type": RealtimeFeedbackType.NODE_TRANSITION.value,
            "payload": {
                "node_id": node_id,
                "node_name": node_name,
                "previous_node_id": previous_node_id,
                "previous_node_name": previous_node_name,
                "allow_interrupt": allow_interrupt,
            },
        }
        # Send via WebSocket if available
        if ws_sender:
            try:
                await ws_sender({**message, "node_id": node_id, "node_name": node_name})
            except Exception as e:
                logger.debug(f"Failed to send node transition via WebSocket: {e}")

        # Always log to in-memory buffer (node_id/node_name injected by buffer's append)
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

    # Check if the workflow has any active recordings so the engine can
    # include recording response mode instructions in all node prompts.
    has_recordings = await db_client.has_active_recordings(workflow.organization_id)

    context_compaction_enabled = (workflow.workflow_configurations or {}).get(
        "context_compaction_enabled", False
    )
    # Context compaction doesn't apply in realtime mode: the speech-to-speech
    # service manages its own conversation state server-side.
    if is_realtime and context_compaction_enabled:
        logger.info("Disabling context_compaction_enabled for realtime workflow run")
        context_compaction_enabled = False

    engine = PipecatEngine(
        llm=llm,
        inference_llm=inference_llm,
        workflow=workflow_graph,
        call_context_vars=merged_call_context_vars,
        workflow_run_id=workflow_run_id,
        node_transition_callback=node_transition_callback,
        embeddings_api_key=embeddings_api_key,
        embeddings_model=embeddings_model,
        embeddings_base_url=embeddings_base_url,
        has_recordings=has_recordings,
        context_compaction_enabled=context_compaction_enabled,
    )

    # Create pipeline components
    audio_buffer, context = create_pipeline_components(audio_config)

    # Set the context, audio_config, and audio_buffer after creation
    engine.set_context(context)
    engine.set_audio_config(audio_config)

    assistant_params = LLMAssistantAggregatorParams(
        correct_aggregation_callback=engine.create_aggregation_correction_callback(),
    )

    # Configure turn strategies based on STT provider, model, and workflow configuration
    if is_realtime:
        # Realtime services do server-side turn detection for response generation,
        # but we still need a client-side stop strategy so the user aggregator emits
        # UserStoppedSpeakingFrame. Without it, downstream consumers (e.g. voicemail
        # detector) and Gemini Live's _finalize_pending flag never see a turn end.
        user_turn_strategies = UserTurnStrategies(
            start=[VADUserTurnStartStrategy()],
            stop=[SpeechTimeoutUserTurnStopStrategy()],
        )

        # Lets not start the pipeline as muted for Realtime
        # - CallbackUserMuteStrategy: mutes based on engine's _mute_pipeline state
        user_mute_strategies = [
            FunctionCallUserMuteStrategy(),
            CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
        ]
    else:
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
                start=[
                    VADUserTurnStartStrategy(),
                    TranscriptionUserTurnStartStrategy(),
                ],
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(params=smart_turn_params)
                    )
                ],
            )
        else:
            # Transcription-based (default): best for short 1-2 word responses
            user_turn_strategies = UserTurnStrategies(
                start=[
                    VADUserTurnStartStrategy(),
                    TranscriptionUserTurnStartStrategy(),
                ],
                stop=[SpeechTimeoutUserTurnStopStrategy()],
            )

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
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
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

    voicemail_detector = None
    recording_router = None

    # Create recording audio fetcher (used by recording router, audio greetings,
    # and audio transition speech)
    fetch_audio = create_recording_audio_fetcher(
        organization_id=workflow.organization_id,
        pipeline_sample_rate=audio_config.pipeline_sample_rate,
    )
    engine.set_fetch_recording_audio(fetch_audio)

    # Voicemail detection works in both modes. In realtime mode the detector sits
    # after the realtime LLM and consumes the TranscriptionFrames it broadcasts;
    # the LLM gate / TTS gate are not used (the realtime LLM responds to audio
    # directly, not LLMContextFrames), so on detection we rely on
    # end_call_with_reason to drop the call.
    voicemail_config = (workflow.workflow_configurations or {}).get(
        "voicemail_detection", {}
    )
    if voicemail_config.get("enabled", False):
        logger.info(f"Voicemail detection enabled for workflow run {workflow_run_id}")
        # Create a separate LLM instance for the voicemail sub-pipeline
        # (can't share with main pipeline as it would mess up frame linking)
        if voicemail_config.get("use_workflow_llm", True):
            voicemail_llm = create_llm_service(user_config)
        else:
            voicemail_llm = create_llm_service_from_provider(
                provider=voicemail_config.get("provider", "openai"),
                model=voicemail_config.get("model", "gpt-4.1"),
                api_key=voicemail_config.get("api_key", ""),
            )

        long_speech_timeout = voicemail_config.get("long_speech_timeout", 8.0)
        custom_system_prompt = voicemail_config.get("system_prompt") or None

        voicemail_detector = VoicemailDetector(
            llm=voicemail_llm,
            long_speech_timeout=long_speech_timeout,
            custom_system_prompt=custom_system_prompt,
        )

        # Register event handler to end task when voicemail is detected
        @voicemail_detector.event_handler("on_voicemail_detected")
        async def _on_voicemail_detected(_processor):
            logger.info(f"Voicemail detected for workflow run {workflow_run_id}")
            await engine.end_call_with_reason(
                reason=EndTaskReason.VOICEMAIL_DETECTED.value,
                abort_immediately=True,
            )

    # Recording router is only meaningful in non-realtime mode (it routes between
    # pre-recorded audio playback and dynamic TTS; realtime LLMs produce audio
    # directly).
    if not is_realtime and has_recordings:
        recording_router = RecordingRouterProcessor(
            audio_sample_rate=audio_config.pipeline_sample_rate,
            fetch_recording_audio=fetch_audio,
        )
        # Warm the recording cache in the background so audio is ready
        # before the first playback request.
        asyncio.create_task(
            warm_recording_cache(
                organization_id=workflow.organization_id,
                pipeline_sample_rate=audio_config.pipeline_sample_rate,
            )
        )

    # Build the pipeline
    if is_realtime:
        pipeline = build_realtime_pipeline(
            transport,
            llm,
            audio_buffer,
            user_context_aggregator,
            assistant_context_aggregator,
            pipeline_engine_callback_processor,
            pipeline_metrics_aggregator,
            voicemail_detector=voicemail_detector,
        )
    else:
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
            recording_router=recording_router,
        )

    # Create pipeline task with audio configuration
    task = create_pipeline_task(pipeline, workflow_run_id, audio_config)

    # Now set the task and transport output on the engine
    engine.set_task(task)
    engine.set_transport_output(transport.output())

    # Initialize the engine to set the initial context with
    # System Prompt and Tools
    await engine.initialize()

    # Add real-time feedback observer (always logs to buffer, streams to WS if available)
    feedback_observer = RealtimeFeedbackObserver(
        ws_sender=ws_sender,
        logs_buffer=in_memory_logs_buffer,
    )
    task.add_observer(feedback_observer)

    # Register latency observer to log user-to-bot response latency
    if task.user_bot_latency_observer:

        @task.user_bot_latency_observer.event_handler("on_latency_measured")
        async def on_latency_measured(observer, latency_seconds):
            message = {
                "type": RealtimeFeedbackType.LATENCY_MEASURED.value,
                "payload": {
                    "latency_seconds": latency_seconds,
                },
            }
            if ws_sender:
                try:
                    ws_message = message
                    if in_memory_logs_buffer.current_node_id:
                        ws_message = {
                            **message,
                            "node_id": in_memory_logs_buffer.current_node_id,
                            "node_name": in_memory_logs_buffer.current_node_name,
                        }
                    await ws_sender(ws_message)
                except Exception as e:
                    logger.debug(f"Failed to send latency via WebSocket: {e}")
            try:
                await in_memory_logs_buffer.append(message)
            except Exception as e:
                logger.error(f"Failed to append latency to logs buffer: {e}")

    # Register turn log handlers for all call types (WebRTC and telephony)
    register_turn_log_handlers(
        in_memory_logs_buffer, user_context_aggregator, assistant_context_aggregator
    )

    # Register event handlers — resolve provider_id for PostHog tracking
    if not user_provider_id:
        user_obj = await db_client.get_user_by_id(user_id)
        user_provider_id = str(user_obj.provider_id) if user_obj else None
    in_memory_audio_buffer = register_event_handlers(
        task,
        transport,
        workflow_run_id,
        engine=engine,
        audio_buffer=audio_buffer,
        in_memory_logs_buffer=in_memory_logs_buffer,
        pipeline_metrics_aggregator=pipeline_metrics_aggregator,
        audio_config=audio_config,
        pre_call_fetch_task=pre_call_fetch_task,
        fetch_recording_audio=fetch_audio,
        user_provider_id=user_provider_id,
    )

    register_audio_data_handler(audio_buffer, workflow_run_id, in_memory_audio_buffer)

    try:
        # Run the pipeline
        loop = asyncio.get_running_loop()
        params = PipelineTaskParams(loop=loop)
        await task.run(params)
        logger.info(f"Task completed for run {workflow_run_id}")
    except asyncio.CancelledError:
        logger.warning("Received CancelledError in _run_pipeline")
    finally:
        await feedback_observer.cleanup()
        logger.debug(f"Cleaned up context providers for workflow run {workflow_run_id}")
