"""Service layer for reusable tool management.

Routes and MCP tools both use this module so validation, credential
scoping, MCP discovery, and analytics stay consistent.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent, ToolCategory
from api.schemas.tool import (
    CreatedByResponse,
    CreateToolRequest,
    McpRefreshResponse,
    ToolResponse,
)
from api.services.posthog_client import capture_event
from api.services.workflow.mcp_tool_session import discover_mcp_tools
from api.services.workflow.tools.mcp_tool import (
    McpDefinitionError,
    validate_mcp_definition,
)


class ToolManagementError(ValueError):
    """Recoverable tool-management error with an MCP/HTTP friendly code."""

    def __init__(self, error_code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


def build_tool_response(tool: Any, include_created_by: bool = False) -> ToolResponse:
    """Build a public response from a ToolModel-like object."""
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


def _credential_uuid_from_definition(definition: dict[str, Any]) -> Optional[str]:
    config = definition.get("config")
    if not isinstance(config, dict):
        return None
    credential_uuid = config.get("credential_uuid")
    return credential_uuid if isinstance(credential_uuid, str) else None


async def fetch_credential(credential_uuid: Optional[str], organization_id: int):
    """Best-effort credential lookup for MCP auth/discovery."""
    if not credential_uuid:
        return None
    try:
        return await db_client.get_credential_by_uuid(credential_uuid, organization_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Tool credential fetch failed: {e}")
        return None


async def validate_tool_credential_references(
    definition: dict[str, Any], *, organization_id: int
) -> None:
    """Ensure credential UUID references belong to the caller's organization."""
    credential_uuid = _credential_uuid_from_definition(definition)
    if not credential_uuid:
        return

    credential = await db_client.get_credential_by_uuid(
        credential_uuid, organization_id
    )
    if not credential:
        raise ToolManagementError(
            "credential_not_found",
            (
                f"Credential '{credential_uuid}' was not found in this organization. "
                "Create it in the UI first, then retry with its credential_uuid."
            ),
            status_code=404,
        )


async def populate_discovered_tools(
    definition: dict[str, Any], *, organization_id: int
) -> dict[str, Any]:
    """Best-effort MCP discovery before saving a tool definition.

    Non-MCP definitions pass through untouched. For MCP definitions, a dead
    server yields ``discovered_tools: []`` and does not block creation.
    """
    if not isinstance(definition, dict) or definition.get("type") != "mcp":
        return definition
    try:
        cfg = validate_mcp_definition(definition)
    except McpDefinitionError:
        return definition

    credential = await fetch_credential(cfg.get("credential_uuid"), organization_id)

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


async def create_tool_for_user(
    request: CreateToolRequest,
    user: UserModel,
    *,
    source: str = "api",
) -> ToolResponse:
    """Create a reusable tool for the authenticated user's selected org."""
    if not user.selected_organization_id:
        raise ToolManagementError(
            "organization_required",
            "No organization selected for the user",
            status_code=400,
        )

    definition = request.definition.model_dump()
    await validate_tool_credential_references(
        definition, organization_id=user.selected_organization_id
    )
    definition = await populate_discovered_tools(
        definition,
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
            "source": source,
            "organization_id": user.selected_organization_id,
        },
    )

    return build_tool_response(tool)


async def refresh_mcp_tool_for_user(
    tool_uuid: str,
    user: UserModel,
) -> McpRefreshResponse:
    """Refresh cached MCP catalog for a tool owned by the user's org."""
    if not user.selected_organization_id:
        raise ToolManagementError(
            "organization_required",
            "No organization selected for the user",
            status_code=400,
        )

    tool = await db_client.get_tool_by_uuid(
        tool_uuid, user.selected_organization_id, include_archived=True
    )
    if not tool:
        raise ToolManagementError("tool_not_found", "Tool not found", status_code=404)
    if tool.category != ToolCategory.MCP.value:
        raise ToolManagementError(
            "not_mcp_tool", "Tool is not an MCP tool", status_code=400
        )

    try:
        cfg = validate_mcp_definition(tool.definition)
    except McpDefinitionError as e:
        raise ToolManagementError(
            "invalid_mcp_definition",
            f"Invalid MCP definition: {e}",
            status_code=400,
        ) from e

    credential = await fetch_credential(
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
