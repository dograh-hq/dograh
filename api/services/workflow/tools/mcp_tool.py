"""Pure helpers for MCP-category tools: definition validation and
LLM-function-name namespacing. No I/O, no MCP protocol here."""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_TIMEOUT_SECS = 30
DEFAULT_SSE_READ_TIMEOUT_SECS = 300


class McpDefinitionError(ValueError):
    """Raised when an MCP tool definition is structurally invalid."""


class McpToolConfig(BaseModel):
    """Configuration for an MCP tool definition."""

    transport: Literal["streamable_http"] = Field(
        default="streamable_http", description="MCP transport protocol"
    )
    url: str = Field(description="MCP server URL (must be http:// or https://)")
    credential_uuid: Optional[str] = Field(
        default=None, description="Reference to ExternalCredentialModel for auth"
    )
    tools_filter: list[str] = Field(
        default_factory=list,
        description="Allowlist of MCP tool names to expose (empty = all tools)",
    )
    timeout_secs: int = Field(
        default=DEFAULT_TIMEOUT_SECS, description="Connection timeout in seconds"
    )
    sse_read_timeout_secs: int = Field(
        default=DEFAULT_SSE_READ_TIMEOUT_SECS,
        description="SSE read timeout in seconds",
    )
    discovered_tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Server-managed cache of the MCP server's tool catalog "
            "[{name, description}]. Populated best-effort by the backend."
        ),
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not isinstance(v, str) or not v.startswith(("http://", "https://")):
            raise ValueError("config.url must be an http(s) URL")
        return v

    @field_validator("tools_filter")
    @classmethod
    def validate_tools_filter(cls, v: list[str]) -> list[str]:
        if not all(isinstance(tool_name, str) for tool_name in v):
            raise ValueError("config.tools_filter must be a list of strings")
        return v


class McpToolDefinition(BaseModel):
    """Persisted MCP tool definition."""

    schema_version: int = Field(default=1, description="Schema version")
    type: Literal["mcp"] = Field(description="Tool type")
    config: McpToolConfig = Field(description="MCP server configuration")


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
