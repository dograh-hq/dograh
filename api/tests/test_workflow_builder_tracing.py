import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from api.mcp_server import tracing as mcp_tracing
from api.mcp_server.auth import authenticate_mcp_request, builder_mcp_user
from api.schemas.workflow_builder import BuilderTurnRequest
from api.services.observability.trace_payloads import trace_json
from api.services.workflow import builder
from api.services.workflow.builder_runtime import tracing


@pytest.fixture(name="captured_traces")
def _captured_traces(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        tracing, "_TRACER", provider.get_tracer("dograh.workflow_builder")
    )
    monkeypatch.setattr(mcp_tracing, "_TRACER", provider.get_tracer("dograh.mcp"))
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_concurrent_tool_calls_join_scoped_turns_and_keep_full_previews(
    captured_traces,
):
    payload = {"valid": True, "code": "some workflow source " * 1000}

    @mcp_tracing.traced_tool
    async def preview():
        user = await authenticate_mcp_request()
        await asyncio.sleep(0)
        return {**payload, "org": user.selected_organization_id}

    session_id = uuid4()

    async def run(org):
        user = SimpleNamespace(id=5, selected_organization_id=org)
        with tracing.builder_trace(
            user=user, session_id=session_id, operation="turn", input={}
        ):
            token = builder_mcp_user.set(user)
            try:
                assert (await preview())["org"] == org
            finally:
                builder_mcp_user.reset(token)
        assert tracing.current_builder_trace.get() is None

    await asyncio.gather(run(1), run(2))
    spans = captured_traces.get_finished_spans()
    roots = {
        span.context.trace_id: span
        for span in spans
        if span.name == "workflow_builder.turn"
    }
    assert len(roots) == 2
    for child in (span for span in spans if span.name == "mcp.preview"):
        root = roots[child.context.trace_id]
        assert child.parent.span_id == root.context.span_id
        assert child.attributes["session.id"] == root.attributes["session.id"]
        assert child.attributes["user.id"] == "5"
        assert (
            child.attributes["mcp.org_id"]
            == root.attributes["langfuse.observation.metadata.dograh_organization_id"]
        )
        assert (
            json.loads(child.attributes["langfuse.observation.output"])["code"]
            == payload["code"]
        )
        assert "langfuse.trace.name" not in child.attributes
        assert "dograh.org_id" not in child.attributes
    assert len({root.attributes["session.id"] for root in roots.values()}) == 2


@pytest.mark.asyncio
async def test_external_mcp_keeps_its_own_root(captured_traces):
    @mcp_tracing.traced_tool
    async def external():
        return {"valid": False, "error": "Fix the graph"}

    with tracing._TRACER.start_as_current_span("unrelated") as parent:
        await external()
    child = next(
        s for s in captured_traces.get_finished_spans() if s.name == "mcp.external"
    )
    assert child.parent is None
    assert child.context.trace_id != parent.get_span_context().trace_id
    assert child.attributes["langfuse.observation.level"] == "WARNING"


@pytest.mark.asyncio
async def test_sse_error_marks_root_failed(captured_traces, monkeypatch):
    monkeypatch.setattr(
        builder,
        "local_step",
        AsyncMock(side_effect=RuntimeError("private-upstream-body")),
    )
    events = [
        e
        async for e in builder.builder_turn(
            SimpleNamespace(id=1, selected_organization_id=2),
            uuid4(),
            BuilderTurnRequest(request_id=uuid4(), message="Build an agent"),
        )
    ]
    assert events[-1]["type"] == "error"
    (root,) = captured_traces.get_finished_spans()
    assert root.status.status_code == trace.StatusCode.ERROR
    assert root.attributes["builder.outcome"] == "error"
    assert "private-upstream-body" not in str(root.attributes)
    assert not root.events
    assert tracing.current_builder_trace.get() is None
    assert builder_mcp_user.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_disconnect_closes_span_and_resets_context(
    captured_traces, monkeypatch, cancel
):
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(builder, "local_step", wait)

    async def consume():
        async with aclosing(
            builder.builder_turn(
                SimpleNamespace(id=1, selected_organization_id=2),
                uuid4(),
                BuilderTurnRequest(request_id=uuid4(), message="Build an agent"),
            )
        ) as events:
            assert (await anext(events))["type"] == "status"
            if cancel:
                await anext(events)
        assert tracing.current_builder_trace.get() is None
        assert builder_mcp_user.get() is None

    task = asyncio.create_task(consume())
    if cancel:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    (root,) = captured_traces.get_finished_spans()
    assert root.attributes["builder.outcome"] == "cancelled"
    assert root.end_time is not None


def test_oversized_trace_payload_is_valid_json_with_explicit_truncation():
    data = json.loads(trace_json({"code": "x" * 300_000}))
    assert data["truncated"] is True
    assert data["original_chars"] > 300_000
    assert len(data["preview"]) == 32_000


def test_exporter_filters_sdk_noise_but_keeps_authoring_spans():
    from unittest.mock import Mock

    from opentelemetry.sdk.trace.export import SpanExportResult

    from api.services.pipecat.tracing_config import _OrgRoutingExporter

    destination = Mock()
    destination.export.return_value = SpanExportResult.SUCCESS
    spans = [
        SimpleNamespace(instrumentation_scope=SimpleNamespace(name=name), attributes={})
        for name in (
            "fastmcp",
            "mcp-python-sdk",
            "dograh.mcp",
            "dograh.workflow_builder",
        )
    ]
    assert _OrgRoutingExporter(destination).export(spans) == SpanExportResult.SUCCESS
    destination.export.assert_called_once_with(spans[2:])
