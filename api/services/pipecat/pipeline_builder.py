import os
from typing import TYPE_CHECKING

from loguru import logger

from api.constants import (
    ENABLE_TRACING,
)
from api.services.pipecat.audio_config import AudioConfig
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.utils.context import turn_var

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


def create_pipeline_components(audio_config: AudioConfig, engine: "PipecatEngine"):
    """Create and return the main pipeline components with proper audio configuration"""
    logger.info(f"Creating pipeline components with audio config: {audio_config}")

    # Use native AudioBufferProcessor for merged audio recording
    audio_buffer = AudioBufferProcessor(
        sample_rate=audio_config.pipeline_sample_rate,
        buffer_size=audio_config.buffer_size_bytes,
    )

    transcript = TranscriptProcessor(
        assistant_correct_aggregation_callback=engine.create_aggregation_correction_callback()
    )

    context = LLMContext()

    return audio_buffer, transcript, context


def build_pipeline(
    transport,
    stt,
    transcript,
    audio_buffer,
    llm,
    tts,
    user_context_aggregator,
    assistant_context_aggregator,
    pipeline_engine_callback_processor,
    stt_mute_filter,
    pipeline_metrics_aggregator,
    user_idle_disconnect,
    voicemail_detector=None,
):
    """Build the main pipeline with all components.

    Args:
        audio_buffer: AudioBufferProcessor that handles both input and output audio recording.
        voicemail_detector: Optional native pipecat VoicemailDetector. When provided,
            inserts voicemail detection after STT. Note: We don't use the TTS gate
            to avoid blocking TTS frames during classification.
    """
    # Build processors list with optional voicemail detection
    processors = [
        transport.input(),  # Transport user input
        stt,  # STT (audio_passthrough=True by default, passes InputAudioRawFrame)
    ]

    # Insert voicemail detector after STT if enabled
    # Note: We intentionally do NOT use voicemail_detector.gate() to allow TTS
    # frames to continue flowing during classification (non-blocking detection)
    if voicemail_detector:
        logger.info("Adding native voicemail detector to pipeline")
        processors.append(voicemail_detector.detector())

    # Continue with the rest of the pipeline
    processors.extend(
        [
            stt_mute_filter,  # STTMuteFilters don't let VAD related events pass through if muted
            user_idle_disconnect,
            transcript.user(),
            user_context_aggregator,
            llm,  # LLM
            pipeline_engine_callback_processor,
            tts,  # TTS
            transport.output(),  # Transport bot output
            audio_buffer,  # AudioBufferProcessor - records both input and output audio
            transcript.assistant(),
            assistant_context_aggregator,  # Assistant spoken responses
            pipeline_metrics_aggregator,
        ]
    )

    return Pipeline(processors)


def create_pipeline_task(pipeline, workflow_run_id, audio_config: AudioConfig = None):
    """Create a pipeline task with appropriate parameters"""
    # Set up pipeline params with audio configuration if provided
    pipeline_params = PipelineParams(
        allow_interruptions=True,
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

    task = PipelineTask(
        pipeline,
        params=pipeline_params,
        enable_tracing=ENABLE_TRACING,
        conversation_id=f"{workflow_run_id}",
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
