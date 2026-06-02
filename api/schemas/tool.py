"""Pydantic schemas for reusable Dograh tools.

These models are the single contract for tool creation/update across the
REST API, generated SDKs, and the MCP authoring surface. Field descriptions
are human/API-facing; ``llm_hint`` JSON schema extras are guidance for LLMs
when the same schema is surfaced through MCP or SDK authoring flows.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.enums import ToolCategory

DEFAULT_MCP_TIMEOUT_SECS = 30
DEFAULT_MCP_SSE_READ_TIMEOUT_SECS = 300

ToolParameterType = Literal["string", "number", "boolean", "object", "array"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ToolCategoryValue = Literal[
    "http_api",
    "end_call",
    "transfer_call",
    "calculator",
    "native",
    "integration",
    "mcp",
]


def _llm_hint(text: str) -> dict[str, str]:
    return {"llm_hint": text}


class ToolParameter(BaseModel):
    """A parameter that the tool accepts from the model at call time."""

    name: str = Field(
        description="Parameter name used as a key in the tool request body.",
        json_schema_extra=_llm_hint(
            "Use a stable snake_case name the agent can naturally fill."
        ),
    )
    type: ToolParameterType = Field(
        description="JSON type for the parameter value.",
        json_schema_extra=_llm_hint(
            "Allowed values are string, number, boolean, object, and array."
        ),
    )
    description: str = Field(
        description="Description shown to the model for this parameter.",
        json_schema_extra=_llm_hint(
            "Write this as an instruction to the agent: what value to provide and when."
        ),
    )
    required: bool = Field(
        default=True,
        description="Whether this parameter is required when the tool is called.",
    )


class PresetToolParameter(BaseModel):
    """A parameter injected by Dograh at runtime."""

    name: str = Field(description="Parameter name used as a key in the request body.")
    type: ToolParameterType = Field(
        description="JSON type for the resolved value.",
        json_schema_extra=_llm_hint(
            "Allowed values are string, number, boolean, object, and array."
        ),
    )
    value_template: str = Field(
        description="Fixed value or template, e.g. {{initial_context.phone_number}}.",
        json_schema_extra=_llm_hint(
            "Use {{initial_context.*}} for call-start context and "
            "{{gathered_context.*}} for values extracted during the call."
        ),
    )
    required: bool = Field(
        default=True,
        description="Whether the parameter must resolve to a non-empty value.",
    )


class HttpApiConfig(BaseModel):
    """Configuration for HTTP API tools."""

    method: HttpMethod = Field(
        description="HTTP method to use for the request.",
        json_schema_extra=_llm_hint("Use one of GET, POST, PUT, PATCH, DELETE."),
    )
    url: str = Field(
        description="Target HTTP or HTTPS URL.",
        json_schema_extra=_llm_hint(
            "Use the final endpoint URL. Authentication belongs in credential_uuid, "
            "not embedded in the URL."
        ),
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Static headers to include with every request.",
        json_schema_extra=_llm_hint(
            "Do not place secrets here. Store secrets in the UI credential manager "
            "and reference them with credential_uuid."
        ),
    )
    credential_uuid: Optional[str] = Field(
        default=None,
        description="Reference to an external credential for request authentication.",
        json_schema_extra=_llm_hint(
            "Use a credential_uuid returned by list_credentials. The MCP flow does "
            "not create credential secrets."
        ),
    )
    parameters: Optional[List[ToolParameter]] = Field(
        default=None,
        description="Parameters the model must provide when calling this tool.",
    )
    preset_parameters: Optional[List[PresetToolParameter]] = Field(
        default=None,
        description=(
            "Parameters injected by Dograh from fixed values or workflow context "
            "templates."
        ),
    )
    timeout_ms: Optional[int] = Field(
        default=5000,
        ge=1,
        description="Request timeout in milliseconds.",
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play after tool execution."
    )
    customMessageType: Optional[Literal["text", "audio"]] = Field(
        default=None, description="Type of custom message."
    )
    customMessageRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for an audio custom message."
    )

    @field_validator("method", mode="before")
    @classmethod
    def validate_method(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("method must be one of GET, POST, PUT, PATCH, DELETE")
        method = v.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("method must be one of GET, POST, PUT, PATCH, DELETE")
        return method


class EndCallConfig(BaseModel):
    """Configuration for End Call tools."""

    messageType: Literal["none", "custom", "audio"] = Field(
        default="none", description="Type of goodbye message."
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before ending the call."
    )
    audioRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for audio goodbye message."
    )
    endCallReason: bool = Field(
        default=False,
        description=(
            "When enabled, the model must provide a reason for ending the call. "
            "The reason is set as call disposition and added to call tags."
        ),
    )
    endCallReasonDescription: Optional[str] = Field(
        default=None,
        description=(
            "Description shown to the model for the reason parameter. Used only "
            "when endCallReason is enabled."
        ),
    )


class TransferCallConfig(BaseModel):
    """Configuration for Transfer Call tools."""

    destination: str = Field(
        description=(
            "Phone number or SIP endpoint to transfer the call to, e.g. "
            "+1234567890 or PJSIP/1234."
        )
    )
    messageType: Literal["none", "custom", "audio"] = Field(
        default="none", description="Type of message to play before transfer."
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before transferring."
    )
    audioRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for audio message before transfer."
    )
    timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Maximum seconds to wait for the destination to answer.",
    )

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        """Validate that destination is a valid E.164 phone number or SIP endpoint."""
        if not v.strip():
            return v

        e164_pattern = r"^\+[1-9]\d{1,14}$"
        sip_pattern = r"^(PJSIP|SIP)/[\w\-\.@]+$"

        is_valid_e164 = re.match(e164_pattern, v)
        is_valid_sip = re.match(sip_pattern, v, re.IGNORECASE)

        if not (is_valid_e164 or is_valid_sip):
            raise ValueError(
                "Destination must be a valid E.164 phone number "
                "(e.g., +1234567890) or SIP endpoint (e.g., PJSIP/1234)"
            )
        return v


class McpToolConfig(BaseModel):
    """Configuration for a customer MCP server tool definition."""

    transport: Literal["streamable_http"] = Field(
        default="streamable_http",
        description="MCP transport protocol.",
    )
    url: str = Field(
        description="MCP server URL. Must use http:// or https://.",
        json_schema_extra=_llm_hint("Use the server's streamable HTTP MCP endpoint."),
    )
    credential_uuid: Optional[str] = Field(
        default=None,
        description="Reference to an external credential for MCP server auth.",
        json_schema_extra=_llm_hint(
            "Use a credential_uuid returned by list_credentials. Credentials are "
            "created by the user in the UI."
        ),
    )
    tools_filter: list[str] = Field(
        default_factory=list,
        description="Allowlist of MCP tool names to expose. Empty exposes all tools.",
        json_schema_extra=_llm_hint(
            "Use exact MCP tool names from the remote server catalog when you need "
            "to restrict the exposed tools."
        ),
    )
    timeout_secs: int = Field(
        default=DEFAULT_MCP_TIMEOUT_SECS,
        ge=0,
        description="Connection timeout in seconds.",
    )
    sse_read_timeout_secs: int = Field(
        default=DEFAULT_MCP_SSE_READ_TIMEOUT_SECS,
        ge=0,
        description="SSE read timeout in seconds.",
    )
    discovered_tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Server-managed cache of the MCP server's tool catalog "
            "[{name, description}]. Populated best-effort by the backend."
        ),
        json_schema_extra=_llm_hint("Do not author this field; the server fills it."),
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


class HttpApiToolDefinition(BaseModel):
    """Tool definition for HTTP API tools."""

    schema_version: int = Field(default=1, description="Schema version.")
    type: Literal["http_api"] = Field(description="Tool type.")
    config: HttpApiConfig = Field(description="HTTP API configuration.")


class EndCallToolDefinition(BaseModel):
    """Tool definition for End Call tools."""

    schema_version: int = Field(default=1, description="Schema version.")
    type: Literal["end_call"] = Field(description="Tool type.")
    config: EndCallConfig = Field(description="End Call configuration.")


class TransferCallToolDefinition(BaseModel):
    """Tool definition for Transfer Call tools."""

    schema_version: int = Field(default=1, description="Schema version.")
    type: Literal["transfer_call"] = Field(description="Tool type.")
    config: TransferCallConfig = Field(description="Transfer Call configuration.")


class CalculatorToolDefinition(BaseModel):
    """Tool definition for Calculator tools."""

    schema_version: int = Field(default=1, description="Schema version.")
    type: Literal["calculator"] = Field(description="Tool type.")


class McpToolDefinition(BaseModel):
    """Persisted MCP tool definition."""

    schema_version: int = Field(default=1, description="Schema version.")
    type: Literal["mcp"] = Field(description="Tool type.")
    config: McpToolConfig = Field(description="MCP server configuration.")


ToolDefinition = Annotated[
    Union[
        HttpApiToolDefinition,
        EndCallToolDefinition,
        TransferCallToolDefinition,
        CalculatorToolDefinition,
        McpToolDefinition,
    ],
    Field(discriminator="type"),
]


class CreateToolRequest(BaseModel):
    """Request schema for creating a reusable tool."""

    name: str = Field(
        max_length=255,
        description="Display name for the tool.",
        json_schema_extra=_llm_hint(
            "Use a concise action-oriented name; this influences the function "
            "name shown to the agent."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Description shown to the agent when deciding whether to call it.",
        json_schema_extra=_llm_hint(
            "State exactly when the agent should call the tool and what result it gets."
        ),
    )
    category: ToolCategoryValue = Field(
        default=ToolCategory.HTTP_API.value,
        description="Tool category. Must match definition.type.",
    )
    icon: Optional[str] = Field(
        default="globe", max_length=50, description="Lucide icon identifier."
    )
    icon_color: Optional[str] = Field(
        default="#3B82F6", max_length=7, description="Hex color for the tool icon."
    )
    definition: ToolDefinition = Field(description="Typed tool definition.")

    @model_validator(mode="before")
    @classmethod
    def default_category_from_definition(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("category"):
            return data
        definition = data.get("definition")
        if isinstance(definition, dict) and definition.get("type"):
            return {**data, "category": definition["type"]}
        return data

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        valid_categories = [c.value for c in ToolCategory]
        if v not in valid_categories:
            raise ValueError(
                f"Invalid category '{v}'. Must be one of: {', '.join(valid_categories)}"
            )
        return v

    @model_validator(mode="after")
    def validate_category_matches_definition(self) -> "CreateToolRequest":
        definition_type = self.definition.type
        if self.category != definition_type:
            raise ValueError(
                f"category '{self.category}' must match definition.type "
                f"'{definition_type}'"
            )
        return self


class UpdateToolRequest(BaseModel):
    """Request schema for updating a reusable tool."""

    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    icon: Optional[str] = Field(default=None, max_length=50)
    icon_color: Optional[str] = Field(default=None, max_length=7)
    definition: Optional[ToolDefinition] = None
    status: Optional[str] = None


class CreatedByResponse(BaseModel):
    """Response schema for the user who created a tool."""

    id: int
    provider_id: str


class ToolResponse(BaseModel):
    """Response schema for a reusable tool."""

    id: int
    tool_uuid: str
    name: str
    description: Optional[str]
    category: str
    icon: Optional[str]
    icon_color: Optional[str]
    status: str
    definition: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime]
    created_by: Optional[CreatedByResponse] = None

    model_config = ConfigDict(from_attributes=True)


class McpRefreshResponse(BaseModel):
    """Result of re-discovering an MCP server's tool catalog."""

    tool_uuid: str
    discovered_tools: list = Field(default_factory=list)
    error: Optional[str] = None
