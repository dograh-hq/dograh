"""Private answer inference must export under the call with LLM attributes."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pipecat.utils.tracing.tracing_context import TracingContext

from api.services.pipecat.answer_classification import MachineSubtype
from api.services.workflow import answer_classification_service as classification
from api.services.workflow.pipecat_engine import PipecatEngine


@pytest.fixture
def tracing(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("pipecat")
    # Keep test spans local without replacing the process-wide tracer provider.
    monkeypatch.setattr(
        classification, "trace", SimpleNamespace(get_tracer=lambda _: tracer)
    )
    yield tracer, exporter
    provider.shutdown()


def classifier_llm(response="SCREENER"):
    return SimpleNamespace(
        _settings=SimpleNamespace(model="classifier-test-model"),
        run_inference=AsyncMock(return_value=response),
    )


@pytest.mark.asyncio
async def test_classifier_records_generation_input_output_and_model(tracing):
    _, exporter = tracing
    llm = classifier_llm(' {"subtype": "SCREENER"} ')
    service = classification.AnswerClassificationService(llm)

    assert await service.classify("An ambiguous answer") == MachineSubtype.SCREENER

    (span,) = exporter.get_finished_spans()
    assert span.name == "llm-answer-classifier"
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["gen_ai.request.model"] == "classifier-test-model"
    assert span.attributes["gen_ai.request.max_tokens"] == classification._MAX_TOKENS
    assert span.attributes["stream"] is False
    assert json.loads(span.attributes["input"]) == {
        "messages": [
            {"role": "system", "content": classification._SYSTEM_PROMPT},
            {"role": "user", "content": "An ambiguous answer"},
        ]
    }
    assert json.loads(span.attributes["output"]) == {
        "content": ' {"subtype": "SCREENER"} '
    }
    assert span.attributes["answer.subtype"] == "SCREENER"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_workflow_llm", [False, True])
async def test_factory_classifier_follows_call_and_current_turn(
    tracing, monkeypatch, use_workflow_llm
):
    from pipecat.processors.aggregators.llm_context import LLMContext

    from api.services.pipecat import run_pipeline

    tracer, exporter = tracing
    llm = classifier_llm()
    monkeypatch.setattr(run_pipeline, "create_llm_service", lambda *a, **kw: llm)
    monkeypatch.setattr(
        run_pipeline, "create_llm_service_from_provider", lambda **kw: llm
    )
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=2424)
    supervisor = run_pipeline._create_answer_supervisor(
        {"enabled": True, "use_workflow_llm": use_workflow_llm},
        call_direction="outbound",
        is_realtime=False,
        start_node=None,
        context=LLMContext(),
        user_config=None,
        correlation_id="test-run",
        get_parent_context=engine._get_otel_context,
    )
    # Runtime tracing is installed after supervisor construction. Resolve it at
    # inference time, including when a screening rearm advances the current turn.
    tracing_context = TracingContext()
    engine.task = SimpleNamespace(_tracing_context=tracing_context)
    conversation = tracer.start_span("conversation")
    tracing_context.set_conversation_context(conversation.get_span_context())
    turn = tracer.start_span(
        "turn-1", context=tracing_context.get_conversation_context()
    )
    unrelated = tracer.start_span("unrelated-request")
    try:
        # A background classifier must use the call, not an ambient request span.
        with trace.use_span(unrelated, end_on_exit=False):
            await supervisor._classify_turn("An ambiguous first answer", 0)
            tracing_context.set_turn_context(turn.get_span_context())
            await supervisor._classify_turn("An ambiguous second answer", 0)
        first, second = exporter.get_finished_spans()
        assert first.context.trace_id == conversation.get_span_context().trace_id
        assert first.parent.span_id == conversation.get_span_context().span_id
        assert second.context.trace_id == conversation.get_span_context().trace_id
        assert second.parent.span_id == turn.get_span_context().span_id
    finally:
        await supervisor.close()
        unrelated.end()
        turn.end()
        conversation.end()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("provider failed"), asyncio.CancelledError()]
)
async def test_failed_or_cancelled_inference_ends_span_with_request_metadata(
    tracing, failure
):
    _, exporter = tracing
    llm = classifier_llm()
    llm.run_inference.side_effect = failure
    service = classification.AnswerClassificationService(llm)

    with pytest.raises(type(failure)):
        await service.classify("An ambiguous answer")

    (span,) = exporter.get_finished_spans()
    assert span.attributes["gen_ai.request.model"] == "classifier-test-model"
    assert "input" in span.attributes
    assert "output" not in span.attributes
    assert "answer.subtype" not in span.attributes
    if isinstance(failure, Exception):
        assert span.status.status_code == trace.StatusCode.ERROR
