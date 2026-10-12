"""Inspect exported spans from real provider services using offline streams."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from google.genai.types import FunctionCall, Part
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

from api.tests.test_fallback_llm_processor import Stream, chunk, generate
from api.tests.test_llm_fallback_providers import openai_chunk, rig


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Isolate tracing without replacing the process-wide provider or exporting
    # test traffic through the application's configured Langfuse connection.
    monkeypatch.setattr(
        "pipecat.utils.tracing.service_decorators.trace.get_tracer",
        provider.get_tracer,
    )
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_gemini_fallback_exports_schema_and_recognizable_tool_call(
    monkeypatch, spans
):
    tool = FunctionSchema(
        name="fetch_report_of_user",
        description="Fetch the report of the user",
        properties={
            "name_of_customer": {
                "type": "string",
                "description": "Name of the customer",
            }
        },
        required=["name_of_customer"],
    )
    context = LLMContext(
        [{"role": "user", "content": "Fetch Abhishek's report."}],
        tools=ToolsSchema(standard_tools=[tool]),
    )
    async with rig(monkeypatch, context=context) as h:
        for service in h.services:
            service._tracing_enabled = True
        h.install(0, Stream(openai_chunk(role="assistant"), asyncio.Event()))
        h.install(
            1,
            Stream(
                chunk(
                    parts=[
                        Part(
                            function_call=FunctionCall(
                                id="report-1",
                                name=tool.name,
                                args={"name_of_customer": "Abhishek"},
                            )
                        )
                    ]
                )
            ),
        )
        executed = asyncio.Event()

        async def handler(params):
            executed.set()

        h.llm.register_function(tool.name, handler)
        await generate(h, context=context)
        await asyncio.wait_for(executed.wait(), 1)

    exported = spans.get_finished_spans()
    assert len(exported) == 2
    primary = next(
        s for s in exported if s.attributes["gen_ai.request.model"] == "gpt-4.1"
    )
    backup = next(s for s in exported if s is not primary)
    assert primary.attributes["langfuse.observation.status_message"] == "Cancelled"
    assert (
        primary.attributes["langfuse.observation.metadata.request_status"]
        == "cancelled"
    )
    assert primary.status.status_code != StatusCode.ERROR
    assert "output" not in primary.attributes
    assert "langfuse.observation.metadata.request_status" not in backup.attributes

    for span in exported:
        definition = json.loads(span.attributes["input"])["tools"][0]["function"]
        assert definition == tool.to_default_dict()

    # Gemini emits info and started frames for one call. Langfuse must see one
    # assistant tool call, with the name matching the available tool definition.
    messages = json.loads(backup.attributes["output"])
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    calls = messages[0]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["id"] == "report-1"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == tool.name
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "name_of_customer": "Abhishek"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("providers", [("openai", "google"), ("google", "openai")])
async def test_cancelled_attempt_does_not_mark_next_request_cancelled(
    monkeypatch, spans, providers
):
    chunks = {"openai": openai_chunk, "google": chunk}
    async with rig(monkeypatch, providers=providers) as h:
        for service in h.services:
            service._tracing_enabled = True
        original_push = h.services[0].push_frame
        original_usage = h.services[0].start_llm_usage_metrics
        h.install(0, Stream(asyncio.Event()))
        h.install(1, Stream(chunks[providers[1]]("Backup answer")))
        assert await generate(h) == ["Backup answer"]
        assert h.services[0].push_frame == original_push
        assert h.services[0].start_llm_usage_metrics == original_usage

        h.downstream.frames.clear()
        h.install(0, Stream(chunks[providers[0]]("Primary recovered")))
        assert await generate(h) == ["Primary recovered"]

    exported = spans.get_finished_spans()
    assert len(exported) == 3
    cancelled = [
        s
        for s in exported
        if s.attributes.get("langfuse.observation.metadata.request_status")
        == "cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].status.status_code != StatusCode.ERROR
    recovered = next(
        s for s in exported if s.attributes.get("output") == "Primary recovered"
    )
    assert "langfuse.observation.status_message" not in recovered.attributes


@pytest.mark.asyncio
async def test_cancellation_keeps_partial_output_and_propagates(monkeypatch, spans):
    from pipecat.frames.frames import LLMTextFrame
    from pipecat.utils.tracing.service_decorators import traced_llm

    streaming = asyncio.Event()

    class Service:
        _tracing_enabled = True
        push_frame = AsyncMock()

        @traced_llm
        async def generate(self, context):
            await self.push_frame(LLMTextFrame("Partial answer"))
            streaming.set()
            await asyncio.Event().wait()

    service = Service()
    original_push = service.push_frame
    task = asyncio.create_task(service.generate(LLMContext()))
    await asyncio.wait_for(streaming.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.push_frame is original_push
    (span,) = spans.get_finished_spans()
    assert span.attributes["output"] == "Partial answer"
    assert span.attributes["langfuse.observation.status_message"] == "Cancelled"
    assert (
        span.attributes["langfuse.observation.metadata.request_status"] == "cancelled"
    )
    assert span.status.status_code != StatusCode.ERROR
