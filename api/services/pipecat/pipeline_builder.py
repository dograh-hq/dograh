import os

from loguru import logger

from api.services.pipecat.agent_bridge import AGENT_EDGE_EXCLUDED_FRAMES, AgentWorker
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
    user_context_aggregator,
    assistant_context_aggregator,
    call_duration_processor,
    generation_stage,
    pipeline_metrics_aggregator,
    termination_funnel,
    answer_supervisor=None,
):
    """Build the call pipeline: everything that lives for the whole call.

    The generation stage is a slot rather than a fixed set of processors. A
    cascade call fills it with ``[AgentBridgeProcessor(...)]`` and runs the
    real generation stage in a worker per agent visit, so the LLM, TTS and
    recording router never appear here. See
    :func:`build_agent_generation_pipeline` for what goes in that worker.

    Args:
        audio_buffer: AudioBufferProcessor that handles both input and output audio recording.
        call_duration_processor: The call's own clock. Call-scoped, so it stays
            here whatever occupies the generation slot.
        generation_stage: Processors occupying the slot between the user
            aggregator and the output transport.
        answer_supervisor: Optional answer sensor before the user aggregator,
            with its context gate immediately after the aggregator.
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

    processors.append(user_context_aggregator)

    if answer_supervisor is not None:
        processors.append(answer_supervisor.llm_gate())

    processors.extend(
        [
            call_duration_processor,
            *generation_stage,
            transport.output(),  # Transport bot output
            audio_buffer,  # AudioBufferProcessor - records both input and output audio
            assistant_context_aggregator,  # Assistant spoken responses
            pipeline_metrics_aggregator,
        ]
    )

    # A stage the run did not build (no recognition, for instance) leaves a
    # hole rather than a slot to fill with a passthrough.
    return Pipeline([p for p in processors if p is not None])


def build_agent_generation_pipeline(
    llm,
    tts,
    generation_callback_processor,
    recording_router=None,
):
    """Build the generation stage that runs in one agent visit's own worker.

    Slots into the gap :func:`build_pipeline` leaves between the user
    aggregator and the output transport, so the frames arriving at the call
    pipeline's transport are the same as if this ran inline.
    """
    processors = [llm, generation_callback_processor]
    if recording_router:
        processors.append(recording_router)
    processors.append(tts)
    return Pipeline(processors)


def create_agent_worker(
    pipeline,
    name: str,
    audio_config: AudioConfig | None = None,
    *,
    call_tracing_context=None,
    call_worker_name: str,
) -> PipelineWorker:
    """Create the child worker that runs one agent visit's generation stage.

    Starts inactive: an inactive worker is handed no frames from the bus
    (``BaseWorker.accepts_bus_message``), which is what lets a destination
    agent be built and started under the hold ringer without answering the
    caller. The engine activates it at the commit step.

    Args:
        pipeline: The generation pipeline from
            :func:`build_agent_generation_pipeline`.
        name: Unique worker name; the engine uses the visit id.
        audio_config: Call audio configuration. A child gets no ``StartFrame``
            from the call pipeline -- lifecycle frames never cross the bus --
            so its sample rates have to be set here or its TTS will synthesize
            at the wrong rate.
        call_tracing_context: The call worker's ``TracingContext``. An agent's
            LLM and TTS resolve their parent span through it, so without it
            they produce no spans at all and the call's turns come out empty.
            See the note below on why it is injected rather than built here.
    """
    params = PipelineParams(
        enable_metrics=True,
        enable_usage_metrics=True,
        send_initial_empty_metrics=False,
        # The call pipeline owns the call timer and the idle watchdog; a child
        # that also ran them would end the call on its own schedule.
        enable_heartbeats=False,
    )
    if audio_config:
        params.audio_in_sample_rate = audio_config.transport_in_sample_rate
        params.audio_out_sample_rate = audio_config.transport_out_sample_rate

    worker = AgentWorker(
        pipeline,
        call_worker_name=call_worker_name,
        name=name,
        params=params,
        active=False,
        bridged=(),
        exclude_frames=AGENT_EDGE_EXCLUDED_FRAMES,
        # A retired agent is torn down by the engine, and a live one must not
        # take the call down with it while the caller is still connected.
        idle_timeout_secs=None,
        processor_unusable_policy=ProcessorUnusablePolicy.CONTINUE,
        # The call pipeline holds the one canonical turn tracker; a child
        # tracking turns of its own would double-count every turn and open a
        # second conversation span per visit.
        enable_turn_tracking=False,
        # Tracing has to be on for the services, not just for the worker: the
        # `@traced_llm`/`@traced_tts` decorators read `_tracing_enabled`, which
        # a service takes from this flag through `FrameProcessorSetup`, and
        # return early before they ever look at a tracing context. Left off, a
        # child's LLM and TTS emit no spans however well parented they would
        # have been.
        enable_tracing=call_tracing_context is not None,
        enable_rtvi=False,
    )

    # Turning tracing on above does not give this worker a ``TracingContext``
    # of its own: it only builds one when it is also tracking turns, which is
    # exactly what a child must not do. So the call's context is shared in,
    # which is the better arrangement anyway -- one turn tracker for the call,
    # and every visit's generations recorded inside the turn they belong to.
    # It holds plain attributes rather than context vars, so the call worker's
    # turn observer writes and every child reads the same live object. The
    # worker reads it when it starts, so assigning it here is in time.
    if call_tracing_context is not None:
        worker._tracing_context = call_tracing_context

    return worker


def build_realtime_pipeline(
    transport,
    realtime_llm,
    audio_buffer,
    user_context_aggregator,
    assistant_context_aggregator,
    call_duration_processor,
    agent_generation_processor,
    pipeline_metrics_aggregator,
    termination_funnel,
):
    """Build a pipeline for realtime (speech-to-speech) LLM services.

    Realtime services (e.g. OpenAI Realtime, Gemini Live) handle STT+LLM+TTS
    internally, so no separate STT or TTS processors are needed. There is no
    generation stage to lift out either: the one service consumes the caller's
    audio directly, so a realtime call has no agent worker and its generation
    processor runs here alongside the call's clock.
    """
    processors = [
        transport.input(),
        termination_funnel,
        user_context_aggregator,
        realtime_llm,
    ]

    processors.extend(
        [
            agent_generation_processor,
            call_duration_processor,
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
    name: str | None = None,
    conversation_parent_context=None,
    conversation_type: str = "voice",
    additional_span_attributes: dict | None = None,
):
    """Create a pipeline task with appropriate parameters.

    Args:
        pipeline: The pipeline to run.
        workflow_run_id: Run id, used as the conversation id.
        audio_config: Optional audio configuration.
        name: Worker name. Required when agent workers address this one over
            the bus; otherwise a generated name is fine.
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
        name=name,
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
