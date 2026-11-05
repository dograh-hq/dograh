"""Transfer introductions use inherited context and fail open within a budget."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.tool import TransferCallConfig
from api.services.pipecat.audio_config import AudioConfig
from api.services.workflow import transfer_introduction as introduction


@pytest.fixture(autouse=True)
def tts_factory(monkeypatch):
    factory = Mock(return_value=object())
    monkeypatch.setattr(introduction, "create_tts_service", factory)
    return factory


@pytest.fixture
def engine():
    return SimpleNamespace(
        active_agent=SimpleNamespace(
            is_realtime=False,
            user_config=SimpleNamespace(tts=SimpleNamespace(provider="cartesia")),
            tts=object(),
            inference_llm=SimpleNamespace(
                run_inference=AsyncMock(
                    return_value="Cliente deseja uma reserva para trinta pessoas."
                )
            ),
        ),
        context=LLMContext(
            messages=[
                {"role": "system", "content": "secret system prompt"},
                {"role": "user", "content": "Handover: caller prefers Portuguese."},
                {"role": "user", "content": "Uma mesa para trinta."},
                {"role": "assistant", "tool_calls": [{"id": "pending"}]},
            ]
        ),
        _audio_config=AudioConfig(8000, 8000),
        _call_context_vars={"mps_correlation_id": "run-42"},
        _get_otel_context=lambda: None,
    )


def test_configuration_is_opt_in():
    assert TransferCallConfig().introduction_enabled is False
    assert "25 words" in TransferCallConfig().introduction_prompt


@pytest.mark.asyncio
async def test_summary_covers_handoff_and_latest_turn_without_mutating_context(
    engine, monkeypatch, tts_factory
):
    synth = AsyncMock(return_value=b"wav")
    store = AsyncMock(return_value="https://api.example.com/clip")
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    monkeypatch.setattr(introduction, "store_transfer_audio", store)
    original = list(engine.context.messages)
    result = await introduction.prepare_transfer_introduction(
        engine, {"introduction_prompt": "Use Brazilian Portuguese."}, 7
    )
    assert result == "https://api.example.com/clip"
    args = engine.active_agent.inference_llm.run_inference.call_args
    history = args.args[0].messages[0]["content"]
    assert "prefers Portuguese" in history and "trinta" in history
    assert "secret system prompt" not in history and "pending" not in history
    assert "Brazilian Portuguese" in args.kwargs["system_instruction"]
    assert engine.context.messages == original
    tts_factory.assert_called_once_with(
        engine.active_agent.user_config,
        engine._audio_config,
        organization_id=7,
        correlation_id="run-42",
    )
    assert synth.call_args.args[0] is tts_factory.return_value
    assert synth.call_args.args[0] is not engine.active_agent.tts
    assert synth.call_args.kwargs["sample_rate"] == 8000
    assert synth.call_args.kwargs["max_duration"] == 15


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", [None, "", "word " * 26, "<speak>hello</speak>", "a" * 301]
)
async def test_unusable_summary_skips_audio(engine, monkeypatch, text):
    engine.active_agent.inference_llm.run_inference.return_value = text
    synth = AsyncMock()
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None
    synth.assert_not_called()
    assert engine.active_agent.inference_llm.run_inference.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["inference", "factory", "synthesis", "storage"])
async def test_preparation_failures_never_escape(
    engine, monkeypatch, stage, tts_factory
):
    synth = AsyncMock(return_value=b"wav")
    store = AsyncMock(return_value="url")
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    monkeypatch.setattr(introduction, "store_transfer_audio", store)
    operation = {
        "inference": engine.active_agent.inference_llm.run_inference,
        "factory": tts_factory,
        "synthesis": synth,
        "storage": store,
    }[stage]
    operation.side_effect = RuntimeError("provider failed")
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None


@pytest.mark.asyncio
async def test_budget_includes_synthesis(engine, monkeypatch):
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(introduction, "synthesize_speech", slow)
    monkeypatch.setattr(introduction, "PREPARATION_TIMEOUT_SECONDS", 0.02)
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_call_cancellation_propagates(engine):
    engine.active_agent.inference_llm.run_inference.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await introduction.prepare_transfer_introduction(engine, {}, 7)


@pytest.mark.asyncio
async def test_realtime_agents_skip_inference_and_synthesis(engine, monkeypatch):
    engine.active_agent.is_realtime = True
    synth = AsyncMock()
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None
    engine.active_agent.inference_llm.run_inference.assert_not_awaited()
    synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_preparation_does_not_require_a_provider_specific_http_adapter(
    engine, monkeypatch
):
    engine.active_agent.user_config.tts.provider = "inworld"
    synth = AsyncMock(return_value=b"wav")
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    monkeypatch.setattr(
        introduction, "store_transfer_audio", AsyncMock(return_value="url")
    )
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) == "url"
    synth.assert_awaited_once()


@pytest.fixture
def tracing(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("pipecat")
    monkeypatch.setattr(
        introduction, "trace", SimpleNamespace(get_tracer=lambda _: tracer)
    )
    yield tracer, exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_rejected_summary_is_retried_and_both_attempts_join_the_call_trace(
    engine, monkeypatch, tracing
):
    tracer, exporter = tracing
    llm = engine.active_agent.inference_llm
    llm._settings = SimpleNamespace(model="test-model")
    rejected = "word " * 26
    accepted = "Caller wants help in Portuguese."
    parent = tracer.start_span("call")
    parent_context = trace.set_span_in_context(parent)
    engine._get_otel_context = lambda: parent_context
    duration_started = asyncio.Event()

    async def infer(context, **kwargs):
        assert not exporter.get_finished_spans() or duration_started.is_set()
        if not duration_started.is_set():
            duration_started.set()
            await asyncio.sleep(0.01)
            return rejected
        return accepted

    llm.run_inference.side_effect = infer
    synth = AsyncMock(return_value=b"wav")
    monkeypatch.setattr(introduction, "synthesize_speech", synth)
    monkeypatch.setattr(
        introduction, "store_transfer_audio", AsyncMock(return_value="url")
    )
    try:
        # An unrelated ambient span must not capture this background inference.
        with tracer.start_as_current_span("unrelated"):
            assert (
                await introduction.prepare_transfer_introduction(engine, {}, 7) == "url"
            )
    finally:
        parent.end()

    spans = [
        s
        for s in exporter.get_finished_spans()
        if s.name == "llm-transfer-introduction"
    ]
    assert len(spans) == 2
    for index, span in enumerate(spans, 1):
        assert span.context.trace_id == parent.get_span_context().trace_id
        assert span.parent.span_id == parent.get_span_context().span_id
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.request.model"] == "test-model"
        assert span.attributes["stream"] is False
        assert span.attributes["transfer.introduction.attempt"] == index
        assert "Call history" in span.attributes["input"]
    assert spans[0].end_time - spans[0].start_time >= 10_000_000
    assert spans[0].attributes["transfer.introduction.result"] == "word_limit"
    assert spans[0].attributes["transfer.introduction.words"] == 26
    assert json.loads(spans[0].attributes["output"]) == {"content": rejected}
    assert spans[1].attributes["transfer.introduction.result"] == "accepted"
    assert json.loads(spans[1].attributes["output"]) == {"content": accepted}
    assert "at most 15 words" in spans[1].attributes["input"]
    assert synth.call_args.args[1] == accepted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("provider failed"), asyncio.CancelledError()]
)
async def test_inference_failures_still_finish_the_generation_span(
    engine, tracing, failure
):
    _, exporter = tracing
    engine.active_agent.inference_llm.run_inference.side_effect = failure
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await introduction.prepare_transfer_introduction(engine, {}, 7)
    else:
        assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None
    (span,) = exporter.get_finished_spans()
    assert span.name == "llm-transfer-introduction"
    assert span.attributes["transfer.introduction.attempt"] == 1
    assert "Call history" in span.attributes["input"]
    assert span.end_time is not None


@pytest.mark.asyncio
async def test_retry_shares_preparation_deadline_and_reports_the_stage(
    engine, monkeypatch
):
    cancelled = asyncio.Event()
    calls = 0

    async def infer(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "word " * 26
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.active_agent.inference_llm.run_inference.side_effect = infer
    monkeypatch.setattr(introduction, "PREPARATION_TIMEOUT_SECONDS", 0.02)
    log = Mock()
    monkeypatch.setattr(introduction, "logger", log)
    assert await introduction.prepare_transfer_introduction(engine, {}, 7) is None
    assert cancelled.is_set()
    assert calls == 2
    log.warning.assert_any_call(
        "Transfer introduction skipped stage={} error={}", "summary", "TimeoutError"
    )
