from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunState
from api.services.campaign.campaign_call_dispatcher import campaign_call_dispatcher
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.in_memory_buffers import (
    InMemoryAudioBuffer,
    InMemoryLogsBuffer,
    InMemoryTranscriptBuffer,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.utils.enums import EndTaskReason


def register_event_handlers(
    task: PipelineTask,
    transport,
    workflow_run_id: int,
    engine: PipecatEngine,
    audio_buffer: AudioBufferProcessor,
    in_memory_logs_buffer: InMemoryLogsBuffer,
    pipeline_metrics_aggregator: PipelineMetricsAggregator,
    audio_config=AudioConfig,
):
    """Register all event handlers for transport and task events.

    Returns:
        Tuple of (in_memory_audio_buffer, in_memory_transcript_buffer) for use by other handlers.
    """
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

    # Track both events to ensure LLM is only triggered after both occur
    ready_state = {
        "pipeline_started": False,
        "client_connected": False,
        "llm_triggered": False,
    }

    async def maybe_trigger_llm():
        """Trigger LLM only after both pipeline_started and client_connected events."""
        if (
            ready_state["pipeline_started"]
            and ready_state["client_connected"]
            and not ready_state["llm_triggered"]
        ):
            ready_state["llm_triggered"] = True
            logger.debug(
                "Both pipeline_started and client_connected received - triggering initial LLM generation"
            )
            await engine.llm.queue_frame(LLMContextFrame(engine.context))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _participant):
        logger.debug("In on_client_connected callback handler")
        await audio_buffer.start_recording()
        ready_state["client_connected"] = True
        await maybe_trigger_llm()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _participant):
        call_disposed = engine.is_call_disposed()

        logger.debug(
            f"In on_client_disconnected callback handler. Call disposed: {call_disposed}"
        )

        # Stop recordings
        await audio_buffer.stop_recording()

        await engine.end_call_with_reason(
            EndTaskReason.USER_HANGUP.value, abort_immediately=True
        )

    @task.event_handler("on_pipeline_started")
    async def on_pipeline_started(_task: PipelineTask, _frame: Frame):
        logger.debug("In on_pipeline_started callback handler")
        ready_state["pipeline_started"] = True
        await maybe_trigger_llm()

    @task.event_handler("on_pipeline_finished")
    async def on_pipeline_finished(
        task: PipelineTask,
        _frame: Frame,
    ):
        logger.debug(f"In on_pipeline_finished callback handler")

        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)

        # Stop recordings
        await audio_buffer.stop_recording()

        gathered_context = await engine.get_gathered_context()

        # Add trace URL if available (must be done before conversation tracing ends)
        if task.turn_trace_observer:
            trace_url = task.turn_trace_observer.get_trace_url()
            if trace_url:
                gathered_context["trace_url"] = trace_url
                logger.debug(f"Added trace URL to gathered_context: {trace_url}")

        # also consider existing gathered context in workflow_run
        gathered_context = {**gathered_context, **workflow_run.gathered_context}

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
            state=WorkflowRunState.COMPLETED.value,
        )

        # Save real-time feedback logs to workflow run
        if not in_memory_logs_buffer.is_empty:
            try:
                feedback_events = in_memory_logs_buffer.get_events()
                await db_client.update_workflow_run(
                    run_id=workflow_run_id,
                    logs={"realtime_feedback_events": feedback_events},
                )
                logger.debug(
                    f"Saved {len(feedback_events)} feedback events to workflow run logs"
                )
            except Exception as e:
                logger.error(f"Error saving realtime feedback logs: {e}", exc_info=True)
        else:
            logger.debug("Logs buffer is empty, skipping save")

        # Release concurrent slot for campaign calls
        if workflow_run and workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)

        # Write buffers to temp files and enqueue combined processing task
        audio_temp_path = None
        transcript_temp_path = None

        try:
            if not in_memory_audio_buffer.is_empty:
                audio_temp_path = await in_memory_audio_buffer.write_to_temp_file()
            else:
                logger.debug("Audio buffer is empty, skipping upload")

            if not in_memory_transcript_buffer.is_empty:
                transcript_temp_path = (
                    await in_memory_transcript_buffer.write_to_temp_file()
                )
            else:
                logger.debug("Transcript buffer is empty, skipping upload")

        except Exception as e:
            logger.error(f"Error preparing buffers for S3 upload: {e}", exc_info=True)

        await enqueue_job(FunctionNames.CALCULATE_WORKFLOW_RUN_COST, workflow_run_id)

        # Combined task: uploads artifacts then runs integrations sequentially
        await enqueue_job(
            FunctionNames.PROCESS_WORKFLOW_COMPLETION,
            workflow_run_id,
            audio_temp_path,
            transcript_temp_path,
        )

    # Return the buffers so they can be passed to other handlers
    return in_memory_audio_buffer, in_memory_transcript_buffer


def register_audio_data_handler(
    audio_buffer: AudioBufferProcessor,
    workflow_run_id,
    in_memory_buffer: InMemoryAudioBuffer,
):
    """Register event handler for audio data"""
    logger.info(f"Registering audio data handler for workflow run {workflow_run_id}")

    @audio_buffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        if not audio:
            return

        # Use in-memory buffer
        try:
            await in_memory_buffer.append(audio)
        except MemoryError as e:
            logger.error(f"Memory buffer full: {e}")
            # Could implement overflow to disk here if needed


def register_transcript_handlers(
    user_aggregator,
    assistant_aggregator,
    workflow_run_id,
    in_memory_buffer: InMemoryTranscriptBuffer,
):
    """Register event handlers for transcript updates on context aggregators.

    Uses the on_user_turn_stopped and on_assistant_turn_stopped events to capture
    transcripts as turns complete, following the event-based pattern.
    """

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        timestamp = f"[{message.timestamp}] " if message.timestamp else ""
        line = f"{timestamp}user: {message.content}\n"
        await in_memory_buffer.append(line)

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        timestamp = f"[{message.timestamp}] " if message.timestamp else ""
        line = f"{timestamp}assistant: {message.content}\n"
        await in_memory_buffer.append(line)
