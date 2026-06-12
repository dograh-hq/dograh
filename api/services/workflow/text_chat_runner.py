import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMAssistantPushAggregationFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSSpeakFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.run_context import set_current_org_id

from api.db import db_client
from api.enums import WorkflowRunMode, WorkflowRunState
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.pipeline_builder import create_pipeline_task
from api.services.pipecat.pipeline_metrics_aggregator import (
    PipelineMetricsAggregator,
)
from api.services.pipecat.recording_audio_cache import create_recording_audio_fetcher
from api.services.pipecat.service_factory import create_llm_service
from api.services.pipecat.tracing_config import (
    build_remote_parent_context,
    get_trace_url,
)
from api.services.pipecat.worker_runner import (
    run_pipeline_worker,
    wait_for_pipeline_worker_started,
)
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph

TEXT_CHAT_CHECKPOINT_VERSION = 1
TEXT_CHAT_TURN_TIMEOUT_SECONDS = 60.0
TEXT_CHAT_IDLE_SETTLE_SECONDS = 0.2
TEXT_CHAT_INTERNAL_CANCEL_REASON = "text_chat_turn_complete"


def text_chat_trace_id(workflow_run_id: int) -> str:
    """Deterministic Langfuse trace id for a text-chat session.

    Each turn runs in its own short-lived pipeline, so there is no single
    long-running task to own the trace the way a voice call does. Deriving the
    id from the run id means every turn re-creates the *same* trace id and all
    per-turn spans land in one shared trace — without persisting extra state
    across the otherwise stateless turn requests.
    """
    digest = hashlib.sha256(f"dograh-text-chat:{workflow_run_id}".encode()).hexdigest()
    return digest[:32]


def default_text_chat_checkpoint() -> dict[str, Any]:
    return {
        "version": TEXT_CHAT_CHECKPOINT_VERSION,
        "anchor_turn_id": None,
        "current_node_id": None,
        "messages": [],
        "gathered_context": {},
        "tool_state": {},
    }


def normalize_text_chat_checkpoint(
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = {
        **default_text_chat_checkpoint(),
        **(checkpoint or {}),
    }
    normalized["messages"] = list(normalized.get("messages") or [])
    normalized["gathered_context"] = dict(normalized.get("gathered_context") or {})
    normalized["tool_state"] = dict(normalized.get("tool_state") or {})
    return normalized


@dataclass
class TextChatTurnExecutionResult:
    assistant_text: str | None
    assistant_created_at: str
    events: list[dict[str, Any]]
    usage: dict[str, Any]
    checkpoint: dict[str, Any]
    gathered_context: dict[str, Any]
    initial_context: dict[str, Any]
    state: str
    is_completed: bool


@dataclass
class _ResponseWindowState:
    active_assistant_segments: int = 0
    active_llm_completions: int = 0
    pending_context_requests: int = 0
    blocking_tool_call_ids: set[str] = field(default_factory=set)
    outputs: list[str] = field(default_factory=list)

    def note_direct_context_request(self) -> None:
        self.pending_context_requests += 1

    def note_upstream_context_request(self) -> None:
        self.pending_context_requests += 1

    def note_llm_start(self) -> None:
        if self.pending_context_requests > 0:
            self.pending_context_requests -= 1
        self.active_llm_completions += 1

    def note_llm_end(self) -> None:
        if self.active_llm_completions > 0:
            self.active_llm_completions -= 1

    def note_assistant_turn_started(self) -> None:
        self.active_assistant_segments += 1

    def note_assistant_turn_stopped(self, content: str) -> None:
        if self.active_assistant_segments > 0:
            self.active_assistant_segments -= 1
        normalized_content = content.strip()
        if normalized_content:
            self.outputs.append(normalized_content)

    def note_function_call_in_progress(self, tool_call_id: str, blocking: bool) -> None:
        if blocking:
            self.blocking_tool_call_ids.add(tool_call_id)

    def note_function_call_result(self, tool_call_id: str) -> None:
        self.blocking_tool_call_ids.discard(tool_call_id)

    @property
    def has_blocking_tool_calls(self) -> bool:
        return bool(self.blocking_tool_call_ids)

    @property
    def frontier_is_idle(self) -> bool:
        return (
            self.pending_context_requests == 0
            and self.active_llm_completions == 0
            and self.active_assistant_segments == 0
            and not self.has_blocking_tool_calls
        )


class _TaskQueueProxy:
    def __init__(self, queue_frame):
        self.queue_frame = queue_frame


class _TextChatCaptureProcessor(FrameProcessor):
    def __init__(self, response_window: _ResponseWindowState) -> None:
        super().__init__()
        self.last_activity_at = time.monotonic()
        self.activity_count = 0
        self.events: list[dict[str, Any]] = []
        self._response_window = response_window

    def _touch(self) -> None:
        self.last_activity_at = time.monotonic()
        self.activity_count += 1

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(
            {
                "type": event_type,
                "created_at": datetime.now(UTC).isoformat(),
                "payload": jsonable_encoder(payload),
            }
        )

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self._touch()

        if isinstance(frame, TTSSpeakFrame):
            text_frame = TextFrame(frame.text)
            text_frame.append_to_context = (
                frame.append_to_context if frame.append_to_context is not None else True
            )
            await self.push_frame(text_frame, direction)
            await self.push_frame(LLMAssistantPushAggregationFrame(), direction)
            return

        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.UPSTREAM:
            self._response_window.note_upstream_context_request()

        if isinstance(frame, TTSStoppedFrame):
            await self.push_frame(frame, direction)
            await self.push_frame(LLMAssistantPushAggregationFrame(), direction)
            return

        if (
            isinstance(frame, LLMFullResponseStartFrame)
            and direction == FrameDirection.DOWNSTREAM
        ):
            self._response_window.note_llm_start()

        if (
            isinstance(frame, LLMFullResponseEndFrame)
            and direction is FrameDirection.DOWNSTREAM
        ):
            self._response_window.note_llm_end()
            await self.push_frame(frame, direction)
            # Text chat has no TTS/output transport, so mixed text+tool responses
            # would otherwise leave function calls waiting forever on a
            # BotStoppedSpeakingFrame that never arrives.
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
            return

        if isinstance(frame, FunctionCallInProgressFrame):
            self._response_window.note_function_call_in_progress(
                tool_call_id=frame.tool_call_id,
                blocking=frame.cancel_on_interruption,
            )
            self._append_event(
                "tool_call_started",
                {
                    "function_name": frame.function_name,
                    "tool_call_id": frame.tool_call_id,
                    "arguments": dict(frame.arguments or {}),
                },
            )
        elif isinstance(frame, FunctionCallResultFrame):
            self._response_window.note_function_call_result(frame.tool_call_id)
            self._append_event(
                "tool_call_result",
                {
                    "function_name": frame.function_name,
                    "tool_call_id": frame.tool_call_id,
                    "result": frame.result,
                },
            )
        elif isinstance(frame, EndFrame):
            self._append_event("session_end", {"reason": frame.reason})
        elif isinstance(frame, CancelFrame):
            if frame.reason != TEXT_CHAT_INTERNAL_CANCEL_REASON:
                self._append_event("session_cancelled", {"reason": frame.reason})

        await self.push_frame(frame, direction)


def _merge_usage_info(
    existing: dict[str, Any] | None,
    delta: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    delta = dict(delta or {})

    merged_llm = dict(merged.get("llm") or {})
    for key, value in (delta.get("llm") or {}).items():
        current = dict(merged_llm.get(key) or {})
        merged_llm[key] = {
            "prompt_tokens": int(current.get("prompt_tokens") or 0)
            + int(value.get("prompt_tokens") or 0),
            "completion_tokens": int(current.get("completion_tokens") or 0)
            + int(value.get("completion_tokens") or 0),
            "total_tokens": int(current.get("total_tokens") or 0)
            + int(value.get("total_tokens") or 0),
            "cache_read_input_tokens": int(current.get("cache_read_input_tokens") or 0)
            + int(value.get("cache_read_input_tokens") or 0),
            "cache_creation_input_tokens": int(
                current.get("cache_creation_input_tokens") or 0
            )
            + int(value.get("cache_creation_input_tokens") or 0),
        }
    merged["llm"] = merged_llm

    for section in ("tts", "stt"):
        merged_section = dict(merged.get(section) or {})
        for key, value in (delta.get(section) or {}).items():
            merged_section[key] = float(merged_section.get(key) or 0) + float(value)
        merged[section] = merged_section

    merged["call_duration_seconds"] = int(
        merged.get("call_duration_seconds") or 0
    ) + int(delta.get("call_duration_seconds") or 0)

    return merged


def merge_text_chat_usage_info(
    existing: dict[str, Any] | None,
    delta: dict[str, Any] | None,
) -> dict[str, Any]:
    return _merge_usage_info(existing, delta)


def _resolve_checkpoint_for_pending_turn(
    session_data: dict[str, Any],
    checkpoint: dict[str, Any] | None,
) -> dict[str, Any]:
    turns = list(session_data.get("turns") or [])
    if not turns:
        return normalize_text_chat_checkpoint(checkpoint)

    pending_turn = turns[-1]
    if pending_turn.get("status") != "pending":
        return normalize_text_chat_checkpoint(checkpoint)

    for turn in reversed(turns[:-1]):
        if turn.get("status") != "completed":
            continue
        stored_checkpoint = turn.get("checkpoint_after_turn")
        if stored_checkpoint:
            return normalize_text_chat_checkpoint(stored_checkpoint)
        break

    return normalize_text_chat_checkpoint(checkpoint)


async def _wait_for_quiescence(
    *,
    capture_processor: _TextChatCaptureProcessor,
    response_window: _ResponseWindowState,
    runner_task: asyncio.Task,
    activity_marker: int,
    timeout_seconds: float = TEXT_CHAT_TURN_TIMEOUT_SECONDS,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        if runner_task.done():
            await runner_task
            return

        if (
            capture_processor.activity_count <= activity_marker
            and response_window.frontier_is_idle
        ):
            await asyncio.sleep(0.05)
            continue

        if (
            response_window.frontier_is_idle
            and (time.monotonic() - capture_processor.last_activity_at)
            >= TEXT_CHAT_IDLE_SETTLE_SECONDS
        ):
            return

        await asyncio.sleep(0.05)

    raise TimeoutError(
        "Timed out waiting for text chat response window to settle "
        f"(pending_context_requests={response_window.pending_context_requests}, "
        f"active_llm_completions={response_window.active_llm_completions}, "
        f"active_assistant_segments={response_window.active_assistant_segments}, "
        f"blocking_tool_calls={sorted(response_window.blocking_tool_call_ids)})"
    )


async def execute_text_chat_pending_turn(
    *,
    workflow_run_id: int,
    workflow_id: int,
    session_data: dict[str, Any],
    checkpoint: dict[str, Any] | None,
) -> TextChatTurnExecutionResult:
    turns = list(session_data.get("turns") or [])
    if not turns or turns[-1].get("status") != "pending":
        raise ValueError("Text chat session has no pending turn to execute")

    pending_turn = turns[-1]
    pending_user_message = (
        ((pending_turn.get("user_message") or {}).get("text") or "").strip()
        if pending_turn.get("user_message") is not None
        else None
    )

    workflow_run, _ = await db_client.get_workflow_run_with_context(workflow_run_id)
    if not workflow_run or workflow_run.workflow_id != workflow_id:
        raise ValueError("Workflow run not found for text chat execution")
    if workflow_run.definition is None:
        raise ValueError("Workflow run is missing a pinned definition")
    if workflow_run.workflow is None or workflow_run.workflow.user is None:
        raise ValueError("Workflow run is missing workflow context")

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=workflow_run.workflow.organization_id
    )
    if workflow is None:
        raise ValueError("Workflow not found for text chat execution")

    # Stamp the async context so OTEL spans are tagged with this org and routed
    # to its Langfuse project (the voice paths do this in run_pipeline /
    # webrtc_signaling; the text path previously skipped it, so its spans never
    # reached org-specific exporters).
    set_current_org_id(workflow.organization_id)

    run_definition = workflow_run.definition
    run_configs = run_definition.workflow_configurations or {}

    from api.services.configuration.ai_model_configuration import (
        get_effective_ai_model_configuration_for_workflow,
    )

    user_config = await get_effective_ai_model_configuration_for_workflow(
        user_id=workflow_run.workflow.user.id,
        organization_id=workflow.organization_id,
        workflow_configurations=run_configs,
    )
    if user_config.llm is None:
        raise ValueError("Text chat requires an LLM configuration")

    from api.services.managed_model_services import (
        MPS_CORRELATION_ID_CONTEXT_KEY,
        ensure_mps_correlation_id,
    )

    base_initial_context = dict(workflow_run.initial_context or {})
    mps_correlation_id = await ensure_mps_correlation_id(
        ai_model_config=user_config,
        workflow_run_id=workflow_run_id,
        initial_context=base_initial_context,
    )

    llm = create_llm_service(user_config, correlation_id=mps_correlation_id)
    inference_llm = llm

    runtime_configuration = {
        "llm_provider": user_config.llm.provider,
        "llm_model": user_config.llm.model,
    }
    initial_context = {
        **base_initial_context,
        "runtime_configuration": runtime_configuration,
    }
    if mps_correlation_id:
        initial_context[MPS_CORRELATION_ID_CONTEXT_KEY] = mps_correlation_id
    await db_client.update_workflow_run(
        workflow_run_id,
        initial_context=initial_context,
    )

    workflow_graph = WorkflowGraph(
        ReactFlowDTO.model_validate(run_definition.workflow_json)
    )
    base_checkpoint = _resolve_checkpoint_for_pending_turn(session_data, checkpoint)

    response_window = _ResponseWindowState()
    capture_processor = _TextChatCaptureProcessor(response_window)
    context = LLMContext()
    context.set_messages(base_checkpoint["messages"])

    node_transition_events = capture_processor.events

    async def send_node_transition(
        node_id: str,
        node_name: str,
        previous_node_id: str | None,
        previous_node_name: str | None,
        allow_interrupt: bool = False,
    ) -> None:
        node_transition_events.append(
            {
                "type": "node_transition",
                "created_at": datetime.now(UTC).isoformat(),
                "payload": {
                    "node_id": node_id,
                    "node_name": node_name,
                    "previous_node_id": previous_node_id,
                    "previous_node_name": previous_node_name,
                    "allow_interrupt": allow_interrupt,
                },
            }
        )

    embeddings_api_key = None
    embeddings_model = None
    embeddings_base_url = None
    if user_config.embeddings:
        from api.services.configuration.ai_model_configuration import (
            apply_managed_embeddings_base_url,
        )

        embeddings_api_key = user_config.embeddings.api_key
        embeddings_model = user_config.embeddings.model
        embeddings_provider = getattr(user_config.embeddings, "provider", None)
        embeddings_base_url = apply_managed_embeddings_base_url(
            provider=embeddings_provider,
            base_url=getattr(user_config.embeddings, "base_url", None),
        )

    has_recordings = await db_client.has_active_recordings(workflow.organization_id)
    context_compaction_enabled = (workflow.workflow_configurations or {}).get(
        "context_compaction_enabled", False
    )

    engine = PipecatEngine(
        llm=llm,
        inference_llm=inference_llm,
        context=context,
        workflow=workflow_graph,
        call_context_vars=initial_context,
        workflow_run_id=workflow_run_id,
        node_transition_callback=send_node_transition,
        embeddings_api_key=embeddings_api_key,
        embeddings_model=embeddings_model,
        embeddings_base_url=embeddings_base_url,
        has_recordings=has_recordings,
        context_compaction_enabled=context_compaction_enabled,
    )
    engine._gathered_context = dict(base_checkpoint["gathered_context"])

    assistant_params = LLMAssistantAggregatorParams()
    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params
    )
    assistant_context_aggregator = context_aggregator.assistant()

    @assistant_context_aggregator.event_handler("on_assistant_turn_started")
    async def on_assistant_turn_started(_aggregator):
        response_window.note_assistant_turn_started()

    @assistant_context_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message):
        response_window.note_assistant_turn_stopped(message.content or "")

    # Text chat has no wire transport; reuse the neutral 16 kHz config shape
    # from the browser pipeline so TTS/recording helpers still have sane defaults.
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)
    pipeline_metrics_aggregator = PipelineMetricsAggregator()

    # Stitch every per-turn pipeline of this session into one Langfuse trace by
    # handing each task the same remote parent context (derived from the run id).
    trace_id = text_chat_trace_id(workflow_run_id)
    conversation_parent_context = build_remote_parent_context(trace_id)
    # The stitched trace has no real root span (each per-turn conversation span
    # hangs off a synthetic remote parent), so Langfuse can't infer a name and
    # shows "Unnamed trace". Name it explicitly via the conversation span.
    trace_span_attributes = {
        "langfuse.trace.name": workflow_run.name or f"text-chat-{workflow_run_id}"
    }

    pipeline = Pipeline(
        [
            llm,
            capture_processor,
            assistant_context_aggregator,
            pipeline_metrics_aggregator,
        ]
    )
    task = create_pipeline_task(
        pipeline,
        workflow_run_id,
        audio_config,
        conversation_parent_context=conversation_parent_context,
        conversation_type="text",
        additional_span_attributes=trace_span_attributes,
    )
    runner_task = asyncio.create_task(run_pipeline_worker(task))

    engine.set_task(task)
    engine.set_audio_config(audio_config)
    engine.set_transport_output(_TaskQueueProxy(task.queue_frame))
    engine.set_fetch_recording_audio(
        create_recording_audio_fetcher(
            organization_id=workflow.organization_id,
            pipeline_sample_rate=audio_config.pipeline_sample_rate,
        )
    )

    try:
        await wait_for_pipeline_worker_started(task, timeout=5.0, run_task=runner_task)

        await engine.initialize()

        current_node_id = base_checkpoint.get("current_node_id")
        target_node_id = current_node_id or workflow_graph.start_node_id
        await engine.set_node(
            target_node_id,
            emit_transition_event=current_node_id is None,
        )

        opening_marker = capture_processor.activity_count
        opening_expects_llm = pending_user_message is None and (
            current_node_id == target_node_id
            or engine.get_node_greeting(target_node_id) is None
        )
        if opening_expects_llm:
            response_window.note_direct_context_request()
        opening_action = await engine.queue_node_opening(
            node_id=target_node_id,
            previous_node_id=current_node_id,
            generate_if_no_greeting=pending_user_message is None,
        )
        if opening_action != "llm" and opening_expects_llm:
            response_window.pending_context_requests = max(
                0, response_window.pending_context_requests - 1
            )
        if opening_action != "none":
            await _wait_for_quiescence(
                capture_processor=capture_processor,
                response_window=response_window,
                runner_task=runner_task,
                activity_marker=opening_marker,
            )

        if pending_user_message is not None:
            context.add_message({"role": "user", "content": pending_user_message})
            generation_marker = capture_processor.activity_count
            response_window.note_direct_context_request()
            await llm.queue_frame(LLMContextFrame(context))
            await _wait_for_quiescence(
                capture_processor=capture_processor,
                response_window=response_window,
                runner_task=runner_task,
                activity_marker=generation_marker,
            )
    finally:
        if not task.has_finished():
            await task.cancel(reason=TEXT_CHAT_INTERNAL_CANCEL_REASON)
        try:
            await runner_task
        except Exception:
            logger.exception(
                "Transportless text chat pipeline failed while closing run {}",
                workflow_run_id,
            )
            await engine.close_mcp_sessions()
            await engine.cleanup()
            raise
        await engine.close_mcp_sessions()
        await engine.cleanup()

    gathered_context = await engine.get_gathered_context()
    assistant_text = (
        "\n\n".join(part for part in response_window.outputs if part).strip()
        if response_window.outputs
        else None
    )
    assistant_created_at = datetime.now(UTC).isoformat()
    usage = pipeline_metrics_aggregator.get_all_usage_metrics_serialized()
    current_node = getattr(engine, "_current_node", None)

    updated_checkpoint = {
        "version": TEXT_CHAT_CHECKPOINT_VERSION,
        "anchor_turn_id": pending_turn.get("id"),
        "current_node_id": current_node.id if current_node else None,
        "messages": jsonable_encoder(context.get_messages()),
        "gathered_context": jsonable_encoder(gathered_context),
        "tool_state": jsonable_encoder(base_checkpoint.get("tool_state") or {}),
    }

    encoded_gathered_context = jsonable_encoder(gathered_context)
    trace_url = get_trace_url(trace_id, org_id=workflow.organization_id)
    if trace_url:
        encoded_gathered_context = {**encoded_gathered_context, "trace_url": trace_url}

    return TextChatTurnExecutionResult(
        assistant_text=assistant_text,
        assistant_created_at=assistant_created_at,
        events=jsonable_encoder(capture_processor.events),
        usage=jsonable_encoder(usage),
        checkpoint=updated_checkpoint,
        gathered_context=encoded_gathered_context,
        initial_context=jsonable_encoder(initial_context),
        state=(
            WorkflowRunState.COMPLETED.value
            if engine.is_call_disposed()
            else WorkflowRunState.RUNNING.value
        ),
        is_completed=engine.is_call_disposed(),
    )
