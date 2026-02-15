"""API routes for managing tools."""

import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from api.db import db_client
from api.db.models import UserModel
from api.enums import ToolCategory, ToolStatus
from api.services.auth.depends import get_user

router = APIRouter(prefix="/tools")


# Request/Response schemas
class ToolParameter(BaseModel):
    """A parameter that the tool accepts."""

    name: str = Field(description="Parameter name (used as key in request body)")
    type: str = Field(description="Parameter type: string, number, or boolean")
    description: str = Field(description="Description of what this parameter is for")
    required: bool = Field(
        default=True, description="Whether this parameter is required"
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
    timeout_ms: Optional[int] = Field(
        default=5000, description="Request timeout in milliseconds"
    )


class EndCallConfig(BaseModel):
    """Configuration for End Call tools."""

    messageType: Literal["none", "custom"] = Field(
        default="none", description="Type of goodbye message"
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before ending the call"
    )


class TransferCallConfig(BaseModel):
    """Configuration for Transfer Call tools."""

    destination: str = Field(
        description="Phone number to transfer the call to (E.164 format, e.g., +1234567890)"
    )
    messageType: Literal["none", "custom"] = Field(
        default="none", description="Type of message to play before transfer"
    )
    customMessage: Optional[str] = Field(
        default=None, description="Custom message to play before transferring the call"
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
        """Validate that destination is a valid E.164 phone number."""
        # Allow empty string for initial creation (like HTTP API tools with empty URL)
        if not v.strip():
            return v

        # E.164 format: +[1-9]\d{1,14}
        e164_pattern = r"^\+[1-9]\d{1,14}$"
        if not re.match(e164_pattern, v):
            raise ValueError(
                "Destination must be a valid E.164 phone number (e.g., +1234567890)"
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


# Union type for tool definitions - Pydantic will discriminate based on 'type' field
ToolDefinition = Annotated[
    Union[HttpApiToolDefinition, EndCallToolDefinition, TransferCallToolDefinition],
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


@router.get("/")
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

    tool = await db_client.create_tool(
        organization_id=user.selected_organization_id,
        user_id=user.id,
        name=request.name,
        definition=request.definition.model_dump(),
        category=request.category,
        description=request.description,
        icon=request.icon,
        icon_color=request.icon_color,
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

    tool = await db_client.update_tool(
        tool_uuid=tool_uuid,
        organization_id=user.selected_organization_id,
        name=request.name,
        description=request.description,
        definition=request.definition.model_dump() if request.definition else None,
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
