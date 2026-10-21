"""OTel tracing for MCP tool invocations.

The project-wide tracing setup in
`api/services/pipecat/tracing_config.py` already routes spans to
per-organization Langfuse projects based on the `dograh.org_id` span
attribute. This module plugs MCP tool calls into that pipeline:

    @mcp.tool
    @traced_tool
    async def my_tool(...): ...

Each invocation produces one span named `mcp.<tool_name>` with input/output.
Internal builder tools join their turn's trace; external MCP tools start roots.
Authentication stamps identity without opting into per-organization call routing,
so authoring traces use the default developer-facing Langfuse project.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from api.services.observability.trace_payloads import trace_json
from api.services.workflow.builder_runtime.tracing import current_builder_trace

R = TypeVar("R")

_TRACER = trace.get_tracer("dograh.mcp")


def traced_tool(fn: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    """Wrap an MCP tool so each invocation produces a span.

    Captures tool name, input kwargs, output, and exceptions. Stacks
    below `@mcp.tool` so FastMCP sees the wrapped function when
    introspecting the tool schema (`functools.wraps` preserves the
    signature the framework reads).
    """

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> R:
        # Internal builder tools join the server-owned turn. External MCP calls
        # remain independent roots rather than accepting arbitrary client parents.
        builder = current_builder_trace.get()
        with _TRACER.start_as_current_span(
            f"mcp.{fn.__name__}",
            context=builder.parent if builder else Context(),
        ) as span:
            span.set_attribute("mcp.tool.name", fn.__name__)
            # Explicit trace-name override so the Langfuse UI shows
            # `mcp.<tool>` at the top of the trace instead of whatever
            # the framework happens to name the root span.
            if builder:
                span.set_attributes(builder.attributes())
            else:
                span.set_attribute("langfuse.trace.name", f"mcp.{fn.__name__}")
            span.set_attribute("langfuse.observation.type", "tool")
            span.set_attribute(
                "langfuse.observation.input",
                trace_json(kwargs),
            )
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            span.set_attribute(
                "langfuse.observation.output",
                trace_json(result),
            )
            if isinstance(result, dict) and (
                result.get("valid") is False or result.get("error")
            ):
                span.set_attribute("langfuse.observation.level", "WARNING")
                span.set_attribute(
                    "langfuse.observation.status_message",
                    "Tool returned a validation error",
                )
            return result

    return wrapper
