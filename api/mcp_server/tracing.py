"""OTel tracing for MCP tool invocations.

The project-wide tracing setup in
`api/services/pipecat/tracing_config.py` already routes spans to
per-organization Langfuse projects based on the `dograh.org_id` span
attribute. This module plugs MCP tool calls into that pipeline:

    @mcp.tool
    @traced_tool
    async def my_tool(...): ...

Each decorated invocation produces one span named `mcp.<tool_name>` with
Langfuse-rendered input/output. Organization and user attributes are
stamped separately by `authenticate_mcp_request` when it runs inside
the tool body — the decorator's span is the `current_span` at that
point, so the attributes land on the right span and the router export
dispatches to the correct Langfuse project.
"""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

R = TypeVar("R")

_TRACER = trace.get_tracer("dograh.mcp")
# Langfuse truncates long payloads anyway; cap here to keep span size
# bounded. Tune up if you find tool outputs consistently clipped.
_MAX_ATTR_LEN = 8000


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(value)


def traced_tool(fn: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
    """Wrap an MCP tool so each invocation produces a span.

    Captures tool name, input kwargs, output, and exceptions. Stacks
    below `@mcp.tool` so FastMCP sees the wrapped function when
    introspecting the tool schema (`functools.wraps` preserves the
    signature the framework reads).
    """

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> R:
        # Each MCP tool call is its own root trace. Passing an empty
        # `Context()` severs the inherited parent so the span doesn't
        # graft onto whatever other trace happens to be active (e.g.
        # the FastAPI request span, or a client-propagated context).
        # One trace per tool invocation makes Langfuse diffing and
        # per-org filtering clean.
        with _TRACER.start_as_current_span(
            f"mcp.{fn.__name__}",
            context=Context(),
        ) as span:
            span.set_attribute("mcp.tool.name", fn.__name__)
            # Explicit trace-name override so the Langfuse UI shows
            # `mcp.<tool>` at the top of the trace instead of whatever
            # the framework happens to name the root span.
            span.set_attribute("langfuse.trace.name", f"mcp.{fn.__name__}")
            span.set_attribute(
                "langfuse.observation.input",
                _safe_json(kwargs)[:_MAX_ATTR_LEN],
            )
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            span.set_attribute(
                "langfuse.observation.output",
                _safe_json(result)[:_MAX_ATTR_LEN],
            )
            return result

    return wrapper
