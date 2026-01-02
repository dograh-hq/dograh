"""API routes for managing tools."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

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


class ToolDefinition(BaseModel):
    """Tool definition schema."""

    schema_version: int = Field(
        default=1, description="Schema version for compatibility"
    )
    type: str = Field(description="Tool type (http_api)")
    config: HttpApiConfig = Field(description="Tool configuration")


class CreateToolRequest(BaseModel):
    """Request schema for creating a tool."""

    name: str = Field(max_length=255)
    description: Optional[str] = None
    category: str = Field(default=ToolCategory.HTTP_API.value)
    icon: Optional[str] = Field(default="globe", max_length=50)
    icon_color: Optional[str] = Field(default="#3B82F6", max_length=7)
    definition: ToolDefinition


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
    """Validate that the status is valid."""
    valid_statuses = [s.value for s in ToolStatus]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}",
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

    try:
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

    except Exception as e:
        if "unique_org_tool_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"A tool with the name '{request.name}' already exists",
            )
        raise HTTPException(status_code=500, detail=str(e))


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

    try:
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

    except HTTPException:
        raise
    except Exception as e:
        if "unique_org_tool_name" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"A tool with the name '{request.name}' already exists",
            )
        raise HTTPException(status_code=500, detail=str(e))


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
