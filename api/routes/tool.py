"""API routes for managing tools."""

import asyncio
import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent, ToolCategory, ToolStatus
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.posthog_client import capture_event
from api.services.workflow.mcp_tool_session import discover_mcp_tools
from api.services.workflow.tools.mcp_tool import (
    McpDefinitionError,
    validate_mcp_definition,
)
from api.services.workflow.tools.mcp_tool import (
    McpToolConfig as SharedMcpToolConfig,
)
from api.services.workflow.tools.mcp_tool import (
    McpToolDefinition as SharedMcpToolDefinition,
)

router = APIRouter(prefix="/tools")

McpToolConfig = SharedMcpToolConfig
McpToolDefinition = SharedMcpToolDefinition


# Request/Response schemas
class ToolParameter(BaseModel):
    """A parameter that the tool accepts."""

    name: str = Field(description="Parameter name (used as key in request body)")
    type: str = Field(description="Parameter type: string, number, or boolean")
    description: str = Field(description="Description of what this parameter is for")
    required: bool = Field(
        default=True, description="Whether this parameter is required"
    )


class PresetToolParameter(BaseModel):
    """A parameter injected by Dograh at runtime."""

    name: str = Field(description="Parameter name (used as key in request body)")
    type: str = Field(description="Parameter type: string, number, or boolean")
    value_template: str = Field(
        description="Fixed value or template, e.g. {{initial_context.phone_number}}"
    )
    required: bool = Field(
        default=True,
        description="Whether the parameter must resolve to a non-empty value",
    )


class HttpApiConfig(BaseModel):
    """Configuration for HTTP API tools."""

    method: str = Field(description="HTTP method (GET, POST, PUT, PATCH, DELETE)")
    url: str = Field(description="Target URL")
    headers: Optional[Dict[str, str]] = Field(
        default=None, description="Static headers to include"
    )
    credential_uuid: Optional[str] = Field(
        default=None, description="Reference to ExternalCredentialModel for auth"
    )
    parameters: Optional[List[ToolParameter]] = Field(
        default=None, description="Parameters that the tool accepts from LLM"
    )
    preset_parameters: Optional[List[PresetToolParameter]] = Field(
        default=None,
        description="Parameters injected by Dograh from fixed values or workflow context templates",
    )
    timeout_ms: Optional[int] = Field(
        default=5000, description="Request timeout in milliseconds"
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play after tool execution"
    )
    customMessageType: Optional[Literal["text", "audio"]] = Field(
        default=None, description="Type of custom message: text or audio"
    )
    customMessageRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for audio custom message"
    )


class EndCallConfig(BaseModel):
    """Configuration for End Call tools."""

    messageType: Literal["none", "custom", "audio"] = Field(
        default="none", description="Type of goodbye message"
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before ending the call"
    )
    audioRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for audio goodbye message"
    )
    endCallReason: bool = Field(
        default=False,
        description="When enabled, LLM must provide a reason for ending the call. "
        "The reason is set as call disposition and added to call tags.",
    )
    endCallReasonDescription: Optional[str] = Field(
        default=None,
        description="Description shown to the LLM for the reason parameter. "
        "Used only when endCallReason is enabled.",
    )


class TransferCallConfig(BaseModel):
    """Configuration for Transfer Call tools."""

    destination: str = Field(
        description="Phone number or SIP endpoint to transfer the call to (E.164 format e.g., +1234567890, or SIP endpoint e.g., PJSIP/1234)"
    )
    messageType: Literal["none", "custom", "audio"] = Field(
        default="none", description="Type of message to play before transfer"
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before transferring the call"
    )
    audioRecordingId: Optional[str] = Field(
        default=None, description="Recording ID for audio message before transfer"
    )
    timeout: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Maximum time in seconds to wait for destination to answer (5-120 seconds)",
    )

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, v: str) -> str:
        """Validate that destination is a valid E.164 phone number or SIP endpoint."""
        # Allow empty string for initial creation (like HTTP API tools with empty URL)
        if not v.strip():
            return v

        # E.164 format: +[1-9]\d{1,14}
        e164_pattern = r"^\+[1-9]\d{1,14}$"

        # SIP endpoint format: PJSIP/extension or SIP/extension
        sip_pattern = r"^(PJSIP|SIP)/[\w\-\.@]+$"

        is_valid_e164 = re.match(e164_pattern, v)
        is_valid_sip = re.match(sip_pattern, v, re.IGNORECASE)

        if not (is_valid_e164 or is_valid_sip):
            raise ValueError(
                "Destination must be a valid E.164 phone number (e.g., +1234567890) or SIP endpoint (e.g., PJSIP/1234)"
            )
        return v


class HttpApiToolDefinition(BaseModel):
    """Tool definition for HTTP API tools."""

    schema_version: int = Field(default=1, description="Schema version")
    type: Literal["http_api"] = Field(description="Tool type")
    config: HttpApiConfig = Field(description="HTTP API configuration")


class EndCallToolDefinition(BaseModel):
    """Tool definition for End Call tools."""

    schema_version: int = Field(default=1, description="Schema version")
    type: Literal["end_call"] = Field(description="Tool type")
    config: EndCallConfig = Field(description="End Call configuration")


class TransferCallToolDefinition(BaseModel):
    """Tool definition for Transfer Call tools."""

    schema_version: int = Field(default=1, description="Schema version")
    type: Literal["transfer_call"] = Field(description="Tool type")
    config: TransferCallConfig = Field(description="Transfer Call configuration")


class CalculatorToolDefinition(BaseModel):
    """Tool definition for Calculator tools (no configuration needed)."""

    schema_version: int = Field(default=1, description="Schema version")
    type: Literal["calculator"] = Field(description="Tool type")


# Union type for tool definitions - Pydantic will discriminate based on 'type' field
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
    """Request schema for creating a tool."""

    name: str = Field(max_length=255)
    description: Optional[str] = None
    category: str = Field(default=ToolCategory.HTTP_API.value)
    icon: Optional[str] = Field(default="globe", max_length=50)
    icon_color: Optional[str] = Field(default="#3B82F6", max_length=7)
    definition: ToolDefinition

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        """Validate that category is a valid ToolCategory value."""
        valid_categories = [c.value for c in ToolCategory]
        if v not in valid_categories:
            raise ValueError(
                f"Invalid category '{v}'. Must be one of: {', '.join(valid_categories)}"
            )
        return v


class UpdateToolRequest(BaseModel):
    """Request schema for updating a tool."""

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
    """Response schema for a tool."""

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

    class Config:
        from_attributes = True


class McpRefreshResponse(BaseModel):
    """Result of re-discovering an MCP server's tool catalog."""

    tool_uuid: str
    discovered_tools: list = Field(default_factory=list)
    error: Optional[str] = None


def build_tool_response(tool, include_created_by: bool = False) -> ToolResponse:
    """Build a response from a tool model."""
    created_by = None
    if include_created_by and tool.created_by_user:
        created_by = CreatedByResponse(
            id=tool.created_by_user.id,
            provider_id=tool.created_by_user.provider_id,
        )

    return ToolResponse(
        id=tool.id,
        tool_uuid=tool.tool_uuid,
        name=tool.name,
        description=tool.description,
        category=tool.category,
        icon=tool.icon,
        icon_color=tool.icon_color,
        status=tool.status,
        definition=tool.definition,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
        created_by=created_by,
    )


def validate_category(category: str) -> None:
    """Validate that the category is valid."""
    valid_categories = [c.value for c in ToolCategory]
    if category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category '{category}'. Must be one of: {', '.join(valid_categories)}",
        )


def validate_status(status: str) -> None:
    """Validate that the status is valid. Supports comma-separated values."""
    valid_statuses = [s.value for s in ToolStatus]
    status_list = [s.strip() for s in status.split(",")]
    for s in status_list:
        if s not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{s}'. Must be one of: {', '.join(valid_statuses)}",
            )


@router.get(
    "/",
    **sdk_expose(
        method="list_tools",
        description="List tools available to the authenticated organization.",
    ),
)
async def list_tools(
    status: Optional[str] = None,
    category: Optional[str] = None,
    user: UserModel = Depends(get_user),
) -> List[ToolResponse]:
    """
    List all tools for the user's organization.

    Args:
        status: Optional filter by status (active, archived, draft)
        category: Optional filter by category (http_api, native, integration)

    Returns:
        List of tools
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    if status:
        validate_status(status)
    if category:
        validate_category(category)

    tools = await db_client.get_tools_for_organization(
        user.selected_organization_id,
        status=status,
        category=category,
    )

    return [build_tool_response(tool) for tool in tools]


async def _fetch_credential(credential_uuid: Optional[str], organization_id: int):
    """Best-effort credential lookup for MCP auth. A missing/failed credential
    degrades to ``None`` (unauthenticated) rather than failing the request."""
    if not credential_uuid:
        return None
    try:
        return await db_client.get_credential_by_uuid(credential_uuid, organization_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"MCP: credential fetch failed: {e}")
        return None


async def _populate_discovered_tools(definition: dict, *, organization_id: int) -> dict:
    """Best-effort: for an MCP definition, connect to the server, list its
    tools, and overwrite ``config.discovered_tools``. Never raises and never
    blocks tool save — a dead server yields ``discovered_tools: []``. Non-MCP
    definitions pass through untouched."""
    if not isinstance(definition, dict) or definition.get("type") != "mcp":
        return definition
    try:
        cfg = validate_mcp_definition(definition)
    except McpDefinitionError:
        return definition

    credential = await _fetch_credential(cfg.get("credential_uuid"), organization_id)

    # Run discovery in an isolated asyncio task so an anyio cancel-scope
    # CancelledError doesn't bleed into the parent task and corrupt the
    # subsequent DB write. _run() never raises (degrades to []).
    async def _run() -> list:
        try:
            return await discover_mcp_tools(
                url=cfg["url"],
                credential=credential,
                timeout_secs=cfg["timeout_secs"],
                sse_read_timeout_secs=cfg["sse_read_timeout_secs"],
            )
        except BaseException as e:  # noqa: BLE001
            logger.warning(f"MCP discovery failed; caching empty list: {e}")
            return []

    discovered = await asyncio.ensure_future(_run())
    definition["config"]["discovered_tools"] = discovered
    return definition


@router.post("/")
async def create_tool(
    request: CreateToolRequest,
    user: UserModel = Depends(get_user),
) -> ToolResponse:
    """
    Create a new tool.

    Args:
        request: The tool creation request

    Returns:
        The created tool
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    validate_category(request.category)

    definition = await _populate_discovered_tools(
        request.definition.model_dump(),
        organization_id=user.selected_organization_id,
    )

    tool = await db_client.create_tool(
        organization_id=user.selected_organization_id,
        user_id=user.id,
        name=request.name,
        definition=definition,
        category=request.category,
        description=request.description,
        icon=request.icon,
        icon_color=request.icon_color,
    )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.TOOL_CREATED,
        properties={
            "tool_name": request.name,
            "tool_category": request.category,
            "organization_id": user.selected_organization_id,
        },
    )

    return build_tool_response(tool)


@router.get("/{tool_uuid}")
async def get_tool(
    tool_uuid: str,
    user: UserModel = Depends(get_user),
) -> ToolResponse:
    """
    Get a specific tool by UUID.

    Args:
        tool_uuid: The UUID of the tool

    Returns:
        The tool
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    tool = await db_client.get_tool_by_uuid(
        tool_uuid, user.selected_organization_id, include_archived=True
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    return build_tool_response(tool, include_created_by=True)


@router.post("/{tool_uuid}/mcp/refresh")
async def refresh_mcp_tools(
    tool_uuid: str,
    user: UserModel = Depends(get_user),
) -> McpRefreshResponse:
    """Re-discover an MCP tool's server catalog and overwrite the cached
    ``definition.config.discovered_tools``. Server down → 200 with error
    (cache not overwritten on transient failure)."""
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    tool = await db_client.get_tool_by_uuid(
        tool_uuid, user.selected_organization_id, include_archived=True
    )
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.category != ToolCategory.MCP.value:
        raise HTTPException(status_code=400, detail="Tool is not an MCP tool")

    try:
        cfg = validate_mcp_definition(tool.definition)
    except McpDefinitionError as e:
        raise HTTPException(status_code=400, detail=f"Invalid MCP definition: {e}")

    credential = await _fetch_credential(
        cfg.get("credential_uuid"), user.selected_organization_id
    )

    try:
        discovered = await discover_mcp_tools(
            url=cfg["url"],
            credential=credential,
            timeout_secs=cfg["timeout_secs"],
            sse_read_timeout_secs=cfg["sse_read_timeout_secs"],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"MCP refresh discovery failed: {e}")
        discovered = []

    if not discovered:
        error = (
            f"Could not reach the MCP server at {cfg['url']} "
            f"(or it exposes no tools). Previously cached list retained."
        )
        # Do NOT clobber a previously-good cache with [] on a transient outage.
        return McpRefreshResponse(tool_uuid=tool_uuid, discovered_tools=[], error=error)

    new_def = dict(tool.definition or {})
    new_def["config"] = {**new_def.get("config", {}), "discovered_tools": discovered}
    await db_client.update_tool(
        tool_uuid=tool_uuid,
        organization_id=user.selected_organization_id,
        definition=new_def,
    )
    return McpRefreshResponse(
        tool_uuid=tool_uuid, discovered_tools=discovered, error=None
    )


@router.put("/{tool_uuid}")
async def update_tool(
    tool_uuid: str,
    request: UpdateToolRequest,
    user: UserModel = Depends(get_user),
) -> ToolResponse:
    """
    Update a tool.

    Args:
        tool_uuid: The UUID of the tool to update
        request: The update request

    Returns:
        The updated tool
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    if request.status:
        validate_status(request.status)

    definition = (
        await _populate_discovered_tools(
            request.definition.model_dump(),
            organization_id=user.selected_organization_id,
        )
        if request.definition
        else None
    )

    tool = await db_client.update_tool(
        tool_uuid=tool_uuid,
        organization_id=user.selected_organization_id,
        name=request.name,
        description=request.description,
        definition=definition,
        icon=request.icon,
        icon_color=request.icon_color,
        status=request.status,
    )

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    return build_tool_response(tool, include_created_by=True)


@router.delete("/{tool_uuid}")
async def delete_tool(
    tool_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    """
    Archive (soft delete) a tool.

    Args:
        tool_uuid: The UUID of the tool to delete

    Returns:
        Success message
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    deleted = await db_client.archive_tool(tool_uuid, user.selected_organization_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Tool not found")

    return {"status": "archived", "tool_uuid": tool_uuid}


@router.post("/{tool_uuid}/unarchive")
async def unarchive_tool(
    tool_uuid: str,
    user: UserModel = Depends(get_user),
) -> ToolResponse:
    """
    Unarchive a tool (restore from archived state).

    Args:
        tool_uuid: The UUID of the tool to unarchive

    Returns:
        The unarchived tool
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    tool = await db_client.unarchive_tool(tool_uuid, user.selected_organization_id)

    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    return build_tool_response(tool)
