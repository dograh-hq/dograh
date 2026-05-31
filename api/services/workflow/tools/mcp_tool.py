"""Pure helpers for MCP-category tools: definition validation and
LLM-function-name namespacing. No I/O, no MCP protocol here."""

from __future__ import annotations

import re
from typing import Any, Dict

from pydantic import ValidationError

from api.schemas.tool import (
    DEFAULT_MCP_SSE_READ_TIMEOUT_SECS,
    DEFAULT_MCP_TIMEOUT_SECS,
    McpToolDefinition,
)
from api.schemas.tool import (
    McpToolConfig as McpToolConfig,
)

DEFAULT_TIMEOUT_SECS = DEFAULT_MCP_TIMEOUT_SECS
DEFAULT_SSE_READ_TIMEOUT_SECS = DEFAULT_MCP_SSE_READ_TIMEOUT_SECS


class McpDefinitionError(ValueError):
    """Raised when an MCP tool definition is structurally invalid."""


def _format_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


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

    try:
        parsed = McpToolDefinition.model_validate(definition)
    except ValidationError as e:
        raise McpDefinitionError(_format_validation_error(e)) from e

    return parsed.config.model_dump(exclude={"discovered_tools"})


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
