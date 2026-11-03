"""MCP tools for reading and editing reusable Dograh tools."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.schemas.tool import UpdateToolRequest
from api.services.tool_management import (
    ToolManagementError,
    get_tool_for_user,
    update_tool_for_user,
)


@traced_tool
async def get_tool(tool_uuid: str) -> dict[str, Any]:
    """Read a reusable tool's full definition by UUID.

    `list_tools` returns only a summary (tool_uuid, name, description,
    category). Call this to see a tool's complete `definition` before you
    reason about or edit it: method/url/headers/parameters for `http_api`;
    destination/resolver/context-mapping for `transfer_call`; the target
    `workflow_id` for `transfer_agent`; the server url/filters for `mcp`; the
    optional `reason` for `end_call`.

    On success returns `found: true` and the full `tool` (including
    `definition`). On failure returns `found: false`, a machine-readable
    `error_code`, and a human-readable `error`. Possible `error_code` values:
    - `tool_not_found` — no such tool in this organization.
    - `organization_required` — the API key user has no selected organization.
    """
    user = await authenticate_mcp_request()

    try:
        tool = await get_tool_for_user(tool_uuid, user)
    except ToolManagementError as e:
        return {"found": False, "error_code": e.error_code, "error": e.message}

    return {"found": True, "tool": tool.model_dump(mode="json")}


@traced_tool
async def update_tool(tool_uuid: str, request: UpdateToolRequest) -> dict[str, Any]:
    """Edit an existing reusable tool in place, by UUID.

    Supply only the fields you want to change. `definition`, when given, is the
    FULL replacement definition, not a field-level patch: call `get_tool`
    first, change what you need in the returned definition, and send the whole
    thing back. The new definition's `type` must match the tool's current
    category — a tool cannot change category; create a new tool for a different
    type.

    This edits the shared tool object, so the change applies to every workflow
    that references this tool_uuid, not just the one you are editing.

    On success returns `updated: true` and the refreshed tool. On failure
    returns `updated: false`, a machine-readable `error_code`, and a
    human-readable `error`. Possible `error_code` values:
    - `validation_error` — the request failed schema validation.
    - `tool_not_found` — no such tool in this organization.
    - `category_mismatch` — the definition's `type` differs from the tool's
      category; create a new tool instead.
    - `credential_not_found` / `recording_not_found` / `destination_not_found`
      — a referenced resource is not in this organization (same meaning as in
      `create_tool`).
    - `organization_required` — the API key user has no selected organization.
    - `update_failed` — unexpected persistence or backend failure; retry once,
      then surface the error.
    """
    user = await authenticate_mcp_request()

    try:
        parsed_request = UpdateToolRequest.model_validate(request)
    except PydanticValidationError as e:
        return {"updated": False, "error_code": "validation_error", "error": str(e)}

    try:
        tool = await update_tool_for_user(
            tool_uuid, parsed_request, user, source="mcp"
        )
    except ToolManagementError as e:
        return {"updated": False, "error_code": e.error_code, "error": e.message}
    except Exception as e:  # noqa: BLE001
        return {"updated": False, "error_code": "update_failed", "error": str(e)}

    return {"updated": True, "tool": tool.model_dump(mode="json")}
