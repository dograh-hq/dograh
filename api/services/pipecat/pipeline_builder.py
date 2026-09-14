import os

from loguru import logger

from api.services.pipecat.audio_config import AudioConfig
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import (
    PipelineParams,
    PipelineWorker,
    ProcessorUnusablePolicy,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.utils.run_context import turn_var


def create_pipeline_components(audio_config: AudioConfig):
    """Create and return the main pipeline components with proper audio configuration"""
    logger.info(f"Creating pipeline components with audio config: {audio_config}")

    # Use native AudioBufferProcessor for merged audio recording
    audio_buffer = AudioBufferProcessor(
        sample_rate=audio_config.pipeline_sample_rate,
        buffer_size=audio_config.buffer_size_bytes,
    )

    context = LLMContext()

    return audio_buffer, context


def build_pipeline(
    transport,
    stt,
    audio_buffer,
    llm,
    tts,
    user_context_aggregator,
    assistant_context_aggregator,
    pipeline_engine_callback_processor,
    pipeline_metrics_aggregator,
    termination_funnel,
    recording_router=None,
    answer_supervisor=None,
):
    """Build the main pipeline with all components.

    Args:
        audio_buffer: AudioBufferProcessor that handles both input and output audio recording.
        answer_supervisor: Optional answer sensor before the user aggregator,
            with its context gate immediately after the aggregator.
        recording_router: Optional RecordingRouterProcessor. When provided,
            inserts between callback processor and TTS to route between
            pre-recorded audio playback and dynamic TTS.
    """
    # Build processors with optional answer handling.
    #
    # The termination funnel sits directly behind the input transport so every
    # other processor's upstream frames pass through it -- that is the only
    # position from which it can intercept a cancellation on its way to the
    # pipeline worker.
    processors = [
        transport.input(),  # Transport user input
        termination_funnel,
        stt,
    ]

    if answer_supervisor is not None:
        processors.append(answer_supervisor)

    # Continue with the rest of the pipeline
    post_llm = [pipeline_engine_callback_processor]
    if recording_router:
        post_llm.append(recording_router)

    processors.append(user_context_aggregator)

    if answer_supervisor is not None:
        processors.append(answer_supervisor.llm_gate())

    processors.extend(
        [
            llm,  # LLM
            *post_llm,
            tts,  # TTS
            transport.output(),  # Transport bot output
            audio_buffer,  # AudioBufferProcessor - records both input and output audio
            assistant_context_aggregator,  # Assistant spoken responses
            pipeline_metrics_aggregator,
        ]
    )

    return Pipeline(processors)


def build_realtime_pipeline(
    transport,
    realtime_llm,
    audio_buffer,
    user_context_aggregator,
    assistant_context_aggregator,
    pipeline_engine_callback_processor,
    pipeline_metrics_aggregator,
    termination_funnel,
):
    """Build a pipeline for realtime (speech-to-speech) LLM services.

    Realtime services (e.g. OpenAI Realtime, Gemini Live) handle STT+LLM+TTS
    internally, so no separate STT or TTS processors are needed.
    """
    processors = [
        transport.input(),
        termination_funnel,
        user_context_aggregator,
        realtime_llm,
    ]

    processors.extend(
        [
            pipeline_engine_callback_processor,
            transport.output(),
            audio_buffer,
            assistant_context_aggregator,
            pipeline_metrics_aggregator,
        ]
    )

    return Pipeline(processors)


def create_pipeline_task(
    pipeline,
    workflow_run_id,
    audio_config: AudioConfig | None = None,
    *,
    conversation_parent_context=None,
    conversation_type: str = "voice",
    additional_span_attributes: dict | None = None,
):
    """Create a pipeline task with appropriate parameters.

    Args:
        pipeline: The pipeline to run.
        workflow_run_id: Run id, used as the conversation id.
        audio_config: Optional audio configuration.
        conversation_parent_context: Optional OTEL context carrying a fixed
            trace id. When provided, the conversation span attaches to that
            trace instead of starting a new root trace (used by text chat to
            stitch every per-turn pipeline into one trace).
        conversation_type: ``conversation.type`` span attribute value.
        additional_span_attributes: Extra attributes set on the conversation
            span (e.g. ``langfuse.trace.name`` to name a stitched trace that
            has no real root span).
    """
    # Set up pipeline params with audio configuration if provided
    pipeline_params = PipelineParams(
        enable_metrics=True,
        enable_usage_metrics=True,
        send_initial_empty_metrics=False,
        enable_heartbeats=True,
        start_metadata={"workflow_run_id": workflow_run_id},
    )

    # If audio_config is provided, set the audio sample rates
    if audio_config:
        pipeline_params.audio_in_sample_rate = audio_config.transport_in_sample_rate
        pipeline_params.audio_out_sample_rate = audio_config.transport_out_sample_rate
        logger.debug(
            f"Setting pipeline audio params - in: {audio_config.transport_in_sample_rate}Hz, "
            f"out: {audio_config.transport_out_sample_rate}Hz"
        )

    task = PipelineWorker(
        pipeline,
        params=pipeline_params,
        # Pipecat 1.8 replaces ErrorFrame.fatal with processor usability plus
        # a worker policy. A voice/text model that is permanently unusable
        # cannot produce a meaningful Dograh run, so preserve the fork's old
        # fatal-error cancellation behavior through the supported contract.
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        enable_tracing=True,
        enable_rtvi=False,
        conversation_id=f"{workflow_run_id}",
        conversation_parent_context=conversation_parent_context,
        conversation_type=conversation_type,
        additional_span_attributes=additional_span_attributes,
    )

    # Check if turn logging is enabled
    enable_turn_logging = os.getenv("ENABLE_TURN_LOGGING", "false").lower() == "true"

    if enable_turn_logging:
        # Attach event handlers to propagate turn information into the logging context
        turn_observer = task.turn_tracking_observer

        if turn_observer is not None:
            # Import turn context manager only if needed
            from api.services.pipecat.turn_context import get_turn_context_manager

            async def _on_turn_started(observer, turn_number: int):
                """Set the current turn number into the context variable."""
                # Set in both contextvar and turn context manager
                turn_var.set(turn_number)
                turn_manager = get_turn_context_manager()
                turn_manager.set_turn(turn_number)

            # Register the handlers with the observer
            turn_observer.add_event_handler("on_turn_started", _on_turn_started)

    return task
