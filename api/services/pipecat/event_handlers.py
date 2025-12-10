from loguru import logger

from api.db import db_client
from api.services.campaign.call_dispatcher import campaign_call_dispatcher
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_transcript_buffers import (
    InMemoryAudioBuffer,
    InMemoryTranscriptBuffer,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.workflow.disposition_mapper import (
    apply_disposition_mapping,
    get_organization_id_from_workflow_run,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from pipecat.frames.frames import Frame
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.audio_buffer_processor import AudioBuffer
from pipecat.processors.audio.audio_synchronizer import AudioSynchronizer


def register_transport_event_handlers(
    task: PipelineTask,
    transport,
    workflow_run_id,
    engine: PipecatEngine,
    audio_buffer: AudioBuffer,
    audio_synchronizer: AudioSynchronizer,
    audio_config=AudioConfig,
):
    """Register event handlers for transport events"""

    # Initialize in-memory buffers with proper audio configuration
    sample_rate = audio_config.pipeline_sample_rate if audio_config else 16000
    num_channels = 1  # Pipeline audio is always mono

    logger.debug(
        f"Initializing audio buffer for workflow {workflow_run_id} "
        f"with sample_rate={sample_rate}Hz, channels={num_channels}"
    )

    in_memory_audio_buffer = InMemoryAudioBuffer(
        workflow_run_id=workflow_run_id,
        sample_rate=sample_rate,
        num_channels=num_channels,
    )
    in_memory_transcript_buffer = InMemoryTranscriptBuffer(workflow_run_id)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, participant):
        logger.debug("In on_client_connected callback handler - initializing workflow")
        await audio_buffer.start_recording()
        if audio_synchronizer:
            await audio_synchronizer.start_recording()
        await engine.initialize()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, participant):
        call_disposed = engine.is_call_disposed()

        logger.debug(
            f"In on_client_disconnected callback handler. Call disposed: {call_disposed}"
        )
        engine.handle_client_disconnected()

        # Stop recordings
        await audio_buffer.stop_recording()
        if audio_synchronizer:
            await audio_synchronizer.stop_recording()

        # Only cancel the task if the call is not already disposed by the engine
        if not call_disposed:
            await task.cancel()

    # Return the buffers so they can be passed to other handlers
    return in_memory_audio_buffer, in_memory_transcript_buffer


def register_task_event_handler(
    workflow_run_id: int,
    engine: PipecatEngine,
    task: PipelineTask,
    transport,
    audio_buffer: AudioBuffer,
    audio_synchronizer: AudioSynchronizer,
    in_memory_audio_buffer: InMemoryAudioBuffer,
    in_memory_transcript_buffer: InMemoryTranscriptBuffer,
    pipeline_metrics_aggregator: PipelineMetricsAggregator,
):
    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(
        task: PipelineTask,
        frame: Frame,
    ):
        logger.debug(f"In on_pipeline_finished callback handler")

        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)

        # Stop recordings
        await audio_buffer.stop_recording()
        if audio_synchronizer:
            await audio_synchronizer.stop_recording()

        call_disposition = await engine.get_call_disposition()
        logger.debug(f"call disposition in on_pipeline_finished: {call_disposition}")

        gathered_context = await engine.get_gathered_context()

        # also consider existing gathered context in workflow_run
        gathered_context = {**gathered_context, **workflow_run.gathered_context}

        organization_id = await get_organization_id_from_workflow_run(workflow_run_id)
        mapped_call_disposition = await apply_disposition_mapping(
            call_disposition, organization_id
        )

        gathered_context.update({"mapped_call_disposition": mapped_call_disposition})

        # Set user_speech call tag
        if in_memory_transcript_buffer:
            call_tags = gathered_context.get("call_tags", [])

            try:
                has_user_speech = in_memory_transcript_buffer.contains_user_speech()
            except Exception:
                has_user_speech = False

            if has_user_speech and "user_speech" not in call_tags:
                call_tags.append("user_speech")

            # Append any keys from gathered_context that start with 'tag_' to call_tags
            for key in gathered_context:
                if key.startswith("tag_") and key not in call_tags:
                    call_tags.append(gathered_context[key])

            gathered_context["call_tags"] = call_tags

        # Clean up engine resources (including voicemail detector)
        await engine.cleanup()

        # ------------------------------------------------------------------
        # Close Smart-Turn WebSocket if the transport's analyzer supports it
        # ------------------------------------------------------------------
        try:
            turn_analyzer = None

            # Most transports store their params (with turn_analyzer) directly.
            if hasattr(transport, "_params") and transport._params:
                turn_analyzer = getattr(transport._params, "turn_analyzer", None)

            # Fallback: some transports expose params through input() instance.
            if turn_analyzer is None and hasattr(transport, "input"):
                try:
                    input_transport = transport.input()
                    if input_transport and hasattr(input_transport, "_params"):
                        turn_analyzer = getattr(
                            input_transport._params, "turn_analyzer", None
                        )
                except Exception:
                    pass

            if turn_analyzer and hasattr(turn_analyzer, "close"):
                await turn_analyzer.close()
                logger.debug("Closed turn analyzer websocket")
        except Exception as exc:
            logger.warning(f"Failed to close Smart-Turn analyzer gracefully: {exc}")

        usage_info = pipeline_metrics_aggregator.get_all_usage_metrics_serialized()

        logger.debug(f"Usage metrics: {usage_info}")

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            usage_info=usage_info,
            gathered_context=gathered_context,
            is_completed=True,
        )

        # Release concurrent slot for campaign calls
        if workflow_run and workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)

        # Write buffers to temp files and enqueue S3 upload
        try:
            # Only upload if buffers have content
            if not in_memory_audio_buffer.is_empty:
                audio_temp_path = await in_memory_audio_buffer.write_to_temp_file()
                await enqueue_job(
                    FunctionNames.UPLOAD_AUDIO_TO_S3, workflow_run_id, audio_temp_path
                )
            else:
                logger.debug("Audio buffer is empty, skipping upload")

            if not in_memory_transcript_buffer.is_empty:
                transcript_temp_path = (
                    await in_memory_transcript_buffer.write_to_temp_file()
                )
                await enqueue_job(
                    FunctionNames.UPLOAD_TRANSCRIPT_TO_S3,
                    workflow_run_id,
                    transcript_temp_path,
                )
            else:
                logger.debug("Transcript buffer is empty, skipping upload")

        except Exception as e:
            logger.error(f"Error preparing buffers for S3 upload: {e}", exc_info=True)

        await enqueue_job(FunctionNames.CALCULATE_WORKFLOW_RUN_COST, workflow_run_id)
        await enqueue_job(
            FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN, workflow_run_id
        )


def register_audio_data_handler(
    audio_synchronizer, workflow_run_id, in_memory_buffer: InMemoryAudioBuffer
):
    """Register event handler for audio data"""
    logger.info(f"Registering audio data handler for workflow run {workflow_run_id}")

    @audio_synchronizer.event_handler("on_merged_audio")
    async def on_merged_audio(_, pcm, sample_rate, num_channels):
        if not pcm:
            return

        # Use in-memory buffer
        try:
            await in_memory_buffer.append(pcm)
        except MemoryError as e:
            logger.error(f"Memory buffer full: {e}")
            # Could implement overflow to disk here if needed


def register_transcript_handler(
    transcript, workflow_run_id, in_memory_buffer: InMemoryTranscriptBuffer
):
    """Register event handler for transcript updates"""

    @transcript.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        transcript_text = ""
        for msg in frame.messages:
            timestamp = f"[{msg.timestamp}] " if msg.timestamp else ""
            line = f"{timestamp}{msg.role}: {msg.content}\n"
            transcript_text += line

        # Use in-memory buffer
        await in_memory_buffer.append(transcript_text)
