import asyncio

from loguru import logger

from api.db import db_client
from api.enums import PostHogEvent, WorkflowRunState
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_playback import play_audio, play_audio_loop
from api.services.pipecat.in_memory_buffers import (
    InMemoryAudioBuffer,
    InMemoryLogsBuffer,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.tracing_config import get_trace_url
from api.services.posthog_client import capture_event
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.utils.enums import EndTaskReason


async def _capture_call_event(
    workflow_run_id: int,
    user_provider_id: str | None,
    event: str,
    extra_properties: dict | None = None,
) -> None:
    """Look up workflow_run for call metadata and fire a PostHog event.
    Meant to be run via asyncio.create_task() so it never blocks the pipeline."""
    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        properties = {
            "workflow_run_id": workflow_run_id,
            "workflow_id": workflow_run.workflow_id if workflow_run else None,
            "call_type": workflow_run.mode if workflow_run else None,
            "call_direction": (workflow_run.initial_context or {}).get(
                "direction", "outbound"
            )
            if workflow_run
            else None,
        }
        if extra_properties:
            properties.update(extra_properties)
        capture_event(
            distinct_id=user_provider_id,
            event=event,
            properties=properties,
        )
    except Exception:
        logger.exception(f"Background PostHog capture failed for '{event}'")


def register_event_handlers(
    task: PipelineTask,
    transport,
    workflow_run_id: int,
    engine: PipecatEngine,
    audio_buffer: AudioBufferProcessor,
    in_memory_logs_buffer: InMemoryLogsBuffer,
    pipeline_metrics_aggregator: PipelineMetricsAggregator,
    audio_config=AudioConfig,
    pre_call_fetch_task: asyncio.Task | None = None,
    fetch_recording_audio=None,
    user_provider_id: str | None = None,
):
    """Register all event handlers for transport and task events.

    Returns:
        in_memory_audio_buffer for use by other handlers.
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
    # Track both events to ensure the initial response is only triggered after both occur
    ready_state = {
        "pipeline_started": False,
        "client_connected": False,
        "initial_response_triggered": False,
    }

    async def maybe_trigger_initial_response():
        """Start the conversation after both pipeline_started and client_connected events.

        If a pre-call fetch is in progress, plays a ringer while waiting for the
        response, then merges the result into the call context before proceeding.

        If the start node has a greeting configured, play it directly via TTS.
        Otherwise, trigger an LLM generation for the opening message.
        """
        if (
            ready_state["pipeline_started"]
            and ready_state["client_connected"]
            and not ready_state["initial_response_triggered"]
        ):
            ready_state["initial_response_triggered"] = True

            asyncio.create_task(
                _capture_call_event(
                    workflow_run_id, user_provider_id, PostHogEvent.CALL_STARTED
                )
            )

            # Wait for pre-call fetch if in progress, playing ringer meanwhile
            if pre_call_fetch_task is not None:
                if not pre_call_fetch_task.done():
                    logger.info(
                        "Pre-call fetch still in progress, playing ringer while waiting"
                    )
                    stop_ringer = asyncio.Event()
                    sample_rate = audio_config.pipeline_sample_rate or 16000
                    ringer_task = asyncio.create_task(
                        play_audio_loop(
                            stop_event=stop_ringer,
                            sample_rate=sample_rate,
                            queue_frame=transport.output().queue_frame,
                        )
                    )
                    try:
                        fetch_result = await pre_call_fetch_task
                    finally:
                        stop_ringer.set()
                        await ringer_task
                else:
                    fetch_result = pre_call_fetch_task.result()

                if fetch_result:
                    engine._call_context_vars.update(fetch_result)
                    try:
                        await db_client.update_workflow_run(
                            workflow_run_id,
                            initial_context={**engine._call_context_vars},
                        )
                    except Exception as e:
                        logger.error(f"Failed to persist pre-call fetch context: {e}")
                    logger.info(
                        f"Pre-call fetch complete, merged keys: "
                        f"{list(fetch_result.keys())}"
                    )

            # Set the start node now (after pre-call fetch data is merged)
            # so that render_template() has the complete _call_context_vars.
            await engine.set_node(engine.workflow.start_node_id)

            greeting_info = engine.get_start_greeting()
            if greeting_info:
                greeting_type, greeting_value = greeting_info
                if (
                    greeting_type == "audio"
                    and greeting_value
                    and fetch_recording_audio
                ):
                    logger.debug(f"Playing audio greeting recording: {greeting_value}")
                    result = await fetch_recording_audio(
                        recording_pk=int(greeting_value)
                    )
                    if result:
                        await play_audio(
                            result.audio,
                            sample_rate=audio_config.pipeline_sample_rate or 16000,
                            queue_frame=transport.output().queue_frame,
                            transcript=result.transcript,
                            append_to_context=True,
                        )
                    else:
                        logger.warning(
                            f"Failed to fetch audio greeting {greeting_value}, "
                            "falling back to LLM generation"
                        )
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))
                else:
                    logger.debug("Playing text greeting via TTS")
                    # append_to_context=True so the assistant aggregator commits
                    # the greeting to the LLM context once TTS finishes; without
                    # it the LLM would re-greet on its first generation.
                    await task.queue_frame(
                        TTSSpeakFrame(greeting_value, append_to_context=True)
                    )
            else:
                logger.debug(
                    "Both pipeline_started and client_connected received - triggering initial LLM generation"
                )
                await engine.llm.queue_frame(LLMContextFrame(engine.context))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _participant):
        logger.debug("In on_client_connected callback handler")
        await audio_buffer.start_recording()
        ready_state["client_connected"] = True
        await maybe_trigger_initial_response()

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
        await maybe_trigger_initial_response()

    @task.event_handler("on_pipeline_error")
    async def on_pipeline_error(_task: PipelineTask, frame: Frame):
        logger.warning(f"Pipeline error for workflow run {workflow_run_id}: {frame}")
        try:
            workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
            if workflow_run and workflow_run.campaign_id:
                await circuit_breaker.record_and_evaluate(
                    campaign_id=workflow_run.campaign_id, is_failure=True
                )
            asyncio.create_task(
                _capture_call_event(
                    workflow_run_id,
                    user_provider_id,
                    PostHogEvent.CALL_FAILED,
                    extra_properties={"error_reason": "pipeline_error"},
                )
            )
        except Exception as e:
            logger.error(f"Error recording circuit breaker failure: {e}", exc_info=True)

        await engine.end_call_with_reason(
            EndTaskReason.PIPELINE_ERROR.value, abort_immediately=True
        )

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
            trace_id = task.turn_trace_observer.get_trace_id()
            if trace_id:
                trace_url = get_trace_url(trace_id)
                if trace_url:
                    gathered_context["trace_url"] = trace_url
                    logger.debug(f"Added trace URL to gathered_context: {trace_url}")

        # also consider existing gathered context in workflow_run
        gathered_context = {**workflow_run.gathered_context, **gathered_context}

        # Set user_speech call tag
        call_tags = gathered_context.get("call_tags", [])

        try:
            has_user_speech = in_memory_logs_buffer.contains_user_speech()
        except Exception:
            has_user_speech = False

        if has_user_speech and "user_speech" not in call_tags:
            call_tags.append("user_speech")

        # Append any keys from gathered_context that start with 'tag_' to call_tags
        for key in gathered_context:
            if key.startswith("tag_") and key not in call_tags:
                call_tags.append(gathered_context[key])

        gathered_context["call_tags"] = call_tags

        # Store disposition code in workflow for dynamic filtering
        disposition_code = gathered_context.get("mapped_call_disposition")
        if disposition_code and workflow_run:
            try:
                await db_client.add_call_disposition_code(
                    workflow_run.workflow_id, disposition_code
                )
            except Exception as e:
                logger.error(
                    f"Error storing disposition code in workflow: {e}",
                    exc_info=True,
                )

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

        logger.debug(
            f"Usage metrics: {usage_info}, Gathered context: {gathered_context}"
        )

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            usage_info=usage_info,
            gathered_context=gathered_context,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
        )

        asyncio.create_task(
            _capture_call_event(
                workflow_run_id, user_provider_id, PostHogEvent.CALL_COMPLETED
            )
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

        # Write buffers to temp files and enqueue combined processing task
        audio_temp_path = None
        transcript_temp_path = None

        try:
            if not in_memory_audio_buffer.is_empty:
                audio_temp_path = await in_memory_audio_buffer.write_to_temp_file()
            else:
                logger.debug("Audio buffer is empty, skipping upload")

            transcript_temp_path = in_memory_logs_buffer.write_transcript_to_temp_file()
            if not transcript_temp_path:
                logger.debug("No transcript events in logs buffer, skipping upload")

        except Exception as e:
            logger.error(f"Error preparing buffers for S3 upload: {e}", exc_info=True)

        # Combined task: uploads artifacts, runs integrations (including QA),
        # then calculates cost (so QA token usage is captured in usage_info)
        await enqueue_job(
            FunctionNames.PROCESS_WORKFLOW_COMPLETION,
            workflow_run_id,
            audio_temp_path,
            transcript_temp_path,
        )

    # Return the buffer so it can be passed to other handlers
    return in_memory_audio_buffer


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
