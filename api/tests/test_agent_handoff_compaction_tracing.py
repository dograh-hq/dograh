"""Handoff compaction must export an LLM span, including when it falls back.

The summary reaches the model through ``run_inference``, which runs outside
the pipeline and so carries neither the streaming LLM's ``@traced_llm``
decorator nor an ambient span. Compaction also runs on its own task, so the
parent context has to be handed in. These tests pin both, pin that the
fallback paths stay visible -- they are the ones worth finding in Langfuse --
and pin that visibility follows the same per-org policy as every other span.
"""

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from opentelemetry import context as otel_context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.tracing import langfuse_helpers

from api.services.workflow import agent_handoff_context as handoff

SPAN_NAME = "llm-handoff-compaction"


@pytest.fixture
def tracing(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("pipecat")
    # Keep test spans local without replacing the process-wide tracer provider.
    monkeypatch.setattr(handoff, "trace", SimpleNamespace(get_tracer=lambda _: tracer))
    monkeypatch.setattr(handoff, "ensure_tracing", lambda: True)
    yield tracer, exporter
    provider.shutdown()


def conversation(turns: int = 12) -> LLMContext:
    return LLMContext(
        messages=[
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
            for i in range(turns)
        ]
    )


def summarizing_llm(summary="Caller wants a table for four on Friday.", last_index=5):
    async def _generate_summary(frame):
        return summary, last_index

    return SimpleNamespace(
        _settings=SimpleNamespace(model="compaction-test-model"),
        _generate_summary=_generate_summary,
    )


def failing_llm(exc: Exception):
    async def _generate_summary(frame):
        raise exc

    return SimpleNamespace(
        _settings=SimpleNamespace(model="compaction-test-model"),
        _generate_summary=_generate_summary,
    )


def only_span(exporter):
    spans = [s for s in exporter.get_finished_spans() if s.name == SPAN_NAME]
    assert len(spans) == 1, f"expected exactly one {SPAN_NAME} span, got {len(spans)}"
    return spans[0]


@pytest.mark.asyncio
async def test_compaction_records_prompt_output_and_model(tracing):
    _, exporter = tracing
    snapshot = await handoff.build_handoff_snapshot(
        conversation(), summarizing_llm(), request_id="req-success"
    )

    assert snapshot.summarized
    attributes = dict(only_span(exporter).attributes)
    assert attributes["gen_ai.request.model"] == "compaction-test-model"
    assert attributes["stream"] is False
    assert json.loads(attributes["output"])["content"].startswith("Caller wants")
    # The handover prompt is what distinguishes this from ordinary summarization.
    assert "handover note" in attributes["input"]


@pytest.mark.asyncio
async def test_compaction_records_model_from_real_provider_service(tracing):
    from api.services.pipecat.service_factory import create_llm_service_from_provider

    _, exporter = tracing
    llm = create_llm_service_from_provider("openai", "gpt-4.1", "test-key")
    llm.run_inference = AsyncMock(return_value="Caller wants a table for four.")
    try:
        snapshot = await handoff.build_handoff_snapshot(
            conversation(), llm, request_id="req-real-service"
        )
        assert snapshot.summarized
        assert only_span(exporter).attributes["gen_ai.request.model"] == "gpt-4.1"
    finally:
        await llm._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "llm, kwargs, reason",
    [
        (failing_llm(RuntimeError("provider 500")), {}, "error"),
        (summarizing_llm(summary="", last_index=-1), {}, "empty_summary"),
    ],
)
async def test_fallback_is_still_traced(tracing, llm, kwargs, reason):
    _, exporter = tracing
    snapshot = await handoff.build_handoff_snapshot(
        conversation(), llm, request_id=f"req-{reason}", **kwargs
    )

    assert not snapshot.summarized
    span = only_span(exporter)
    assert span.attributes["handoff.fallback_reason"] == reason
    assert span.status.status_code is StatusCode.ERROR
    # Captured before the await, so a failure still shows what was asked for.
    assert "input" in span.attributes


@pytest.mark.asyncio
async def test_timeout_is_still_traced(tracing):
    _, exporter = tracing

    async def _generate_summary(frame):
        await asyncio.sleep(5)
        return "never", 1

    llm = SimpleNamespace(model_name="m", _generate_summary=_generate_summary)
    snapshot = await handoff.build_handoff_snapshot(
        conversation(), llm, request_id="req-timeout", timeout=0.05
    )

    assert not snapshot.summarized
    span = only_span(exporter)
    assert span.attributes["handoff.fallback_reason"] == "timeout"
    assert span.status.status_code is StatusCode.ERROR


@pytest.mark.asyncio
async def test_span_hangs_off_the_caller_context_across_tasks(tracing):
    tracer, exporter = tracing

    with tracer.start_as_current_span("conversation") as parent:
        parent_context = otel_context.get_current()
        parent_span_id = parent.get_span_context().span_id

        async def compact():
            return await handoff.build_handoff_snapshot(
                conversation(),
                summarizing_llm(),
                request_id="req-nested",
                parent_context=parent_context,
            )

        # Mirrors the TaskGroup in AgentTransferCoordinator._run.
        async with asyncio.TaskGroup() as group:
            group.create_task(compact())

    span = only_span(exporter)
    assert span.parent.span_id == parent_span_id
    assert span.context.trace_id == parent.get_span_context().trace_id


@pytest.mark.asyncio
async def test_no_span_when_tracing_is_disabled(tracing, monkeypatch):
    _, exporter = tracing
    monkeypatch.setattr(handoff, "ensure_tracing", lambda: False)

    snapshot = await handoff.build_handoff_snapshot(
        conversation(), summarizing_llm(), request_id="req-off"
    )

    assert snapshot.summarized
    assert not [s for s in exporter.get_finished_spans() if s.name == SPAN_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_point",
    [
        "ensure_tracing",
        "LLMContextSummarizationUtil.format_messages_for_summary",
        "trace.get_tracer",
        "mark_trace_public",
        "add_llm_span_attributes",
    ],
)
async def test_span_setup_failure_preserves_conversation(
    tracing, monkeypatch, failure_point
):
    monkeypatch.setattr(
        f"{handoff.__name__}.{failure_point}",
        Mock(side_effect=RuntimeError("tracing setup failed")),
    )
    context = conversation()
    expected = deepcopy(context.messages)
    context.messages.insert(0, {"role": "system", "content": "Source instructions"})
    context.add_message({"role": "tool", "content": "Source tool result"})
    original = deepcopy(context.messages)
    generate = AsyncMock(return_value=("summary", 5))

    snapshot = await handoff.build_handoff_snapshot(
        context,
        SimpleNamespace(_generate_summary=generate),
        request_id="req-setup-error",
    )

    assert snapshot == handoff.HandoffSnapshot(
        messages=expected, boundary=len(original), summarized=False
    )
    assert context.messages == original
    assert snapshot.messages[0] is not context.messages[1]
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_span_exit_failure_preserves_conversation(tracing, monkeypatch):
    tracer, _ = tracing
    start_span = tracer.start_as_current_span

    @contextmanager
    def failing_exit(*args, **kwargs):
        with start_span(*args, **kwargs) as span:
            yield span
        raise RuntimeError("tracing teardown failed")

    monkeypatch.setattr(tracer, "start_as_current_span", failing_exit)
    context = conversation()
    snapshot = await handoff.build_handoff_snapshot(
        context, summarizing_llm(), request_id="req-exit-error"
    )

    assert snapshot == handoff.HandoffSnapshot(
        messages=context.messages, boundary=len(context.messages), summarized=False
    )


@pytest.mark.asyncio
async def test_fallback_tracing_failure_preserves_conversation(tracing, monkeypatch):
    monkeypatch.setattr(
        handoff, "_record_fallback", Mock(side_effect=RuntimeError("tracing failed"))
    )
    context = conversation()
    snapshot = await handoff.build_handoff_snapshot(
        context,
        failing_llm(RuntimeError("provider failed")),
        request_id="req-record-error",
    )

    assert snapshot == handoff.HandoffSnapshot(
        messages=context.messages, boundary=len(context.messages), summarized=False
    )


@pytest.mark.asyncio
async def test_compaction_cancellation_propagates(tracing):
    started = asyncio.Event()

    async def generate_summary(request):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        handoff.build_handoff_snapshot(
            conversation(),
            SimpleNamespace(_generate_summary=generate_summary),
            request_id="req-cancelled",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def _raising_resolver():
    raise RuntimeError("resolver down")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolver, expected_public",
    [
        (lambda: True, True),
        (lambda: False, False),
        # A resolver that raises must fail closed, like every other span.
        (_raising_resolver, False),
    ],
)
async def test_visibility_follows_the_shared_resolver(
    tracing, monkeypatch, resolver, expected_public
):
    _, exporter = tracing
    monkeypatch.setattr(langfuse_helpers, "_trace_public_resolver", resolver)

    await handoff.build_handoff_snapshot(
        conversation(), summarizing_llm(), request_id="req-visibility"
    )

    span = only_span(exporter)
    assert span.attributes.get("langfuse.trace.public", False) is expected_public


@pytest.mark.asyncio
@pytest.mark.parametrize("env_value, expected_public", [("true", True), ("", False)])
async def test_visibility_falls_back_to_the_env_flag(
    tracing, monkeypatch, env_value, expected_public
):
    """With no resolver installed, the deployment-wide opt-in decides."""
    _, exporter = tracing
    monkeypatch.setattr(langfuse_helpers, "_trace_public_resolver", None)
    monkeypatch.setenv("LANGFUSE_TRACES_PUBLIC", env_value)

    await handoff.build_handoff_snapshot(
        conversation(), summarizing_llm(), request_id="req-visibility-env"
    )

    span = only_span(exporter)
    assert span.attributes.get("langfuse.trace.public", False) is expected_public
