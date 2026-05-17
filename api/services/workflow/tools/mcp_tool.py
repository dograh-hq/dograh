"""Pure helpers for MCP-category tools: definition validation and
LLM-function-name namespacing. No I/O, no MCP protocol here."""

from __future__ import annotations

import re
from typing import Any, Dict

SUPPORTED_TRANSPORTS = {"streamable_http"}
DEFAULT_TIMEOUT_SECS = 30
DEFAULT_SSE_READ_TIMEOUT_SECS = 300


class McpDefinitionError(ValueError):
    """Raised when an MCP tool definition is structurally invalid."""


def validate_mcp_definition(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a ``type: "mcp"`` ToolModel definition and return a
    normalized config dict with defaults applied.

    Raises:
        McpDefinitionError: if the definition is missing required fields
            or uses an unsupported transport.
    """
    if not isinstance(definition, dict) or definition.get("type") != "mcp":
        raise McpDefinitionError("definition.type must be 'mcp'")

    config = definition.get("config")
    if not isinstance(config, dict):
        raise McpDefinitionError("definition.config is required and must be an object")

    url = config.get("url") or ""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise McpDefinitionError("config.url must be an http(s) URL")

    transport = config.get("transport") or "streamable_http"
    if transport not in SUPPORTED_TRANSPORTS:
        raise McpDefinitionError(
            f"config.transport '{transport}' unsupported; allowed: {sorted(SUPPORTED_TRANSPORTS)}"
        )

    tools_filter = config.get("tools_filter") or []
    if not isinstance(tools_filter, list) or not all(
        isinstance(t, str) for t in tools_filter
    ):
        raise McpDefinitionError("config.tools_filter must be a list of strings")

    raw_timeout = config.get("timeout_secs")
    raw_sse = config.get("sse_read_timeout_secs")

    return {
        "url": url,
        "transport": transport,
        "credential_uuid": config.get("credential_uuid") or None,
        "tools_filter": tools_filter,
        "timeout_secs": int(raw_timeout)
        if raw_timeout is not None
        else DEFAULT_TIMEOUT_SECS,
        "sse_read_timeout_secs": int(raw_sse)
        if raw_sse is not None
        else DEFAULT_SSE_READ_TIMEOUT_SECS,
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug


def namespace_function_name(
    tool_name: str, mcp_tool_name: str, *, fallback: str = "server"
) -> str:
    """Build a collision-safe LLM function name: ``mcp__<slug>__<tool>``.

    ``slug`` is derived from the Dograh ToolModel name; if it slugifies to
    empty, ``fallback`` (e.g. first 8 chars of tool_uuid) is used instead.
    """
    slug = _slugify(tool_name) or _slugify(fallback) or "server"
    return f"mcp__{slug}__{mcp_tool_name}"
