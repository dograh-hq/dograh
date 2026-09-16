"""MCP tool for creating reusable Dograh tools."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.schemas.tool import CreateToolRequest
from api.services.tool_management import ToolManagementError, create_tool_for_user


def _error_result(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"created": False, "error_code": code, "error": message, **extra}


@traced_tool
async def create_tool(request: CreateToolRequest) -> dict[str, Any]:
    """Create a reusable tool the agent can invoke during calls.

    The request schema is the same `CreateToolRequest` used by the REST API
    and generated SDKs, and it is authoritative for the tool categories you
    can create: `http_api`, `end_call`, `transfer_call`, `transfer_agent`,
    `calculator`, and `mcp`. Read the `definition` union rather than this
    list if the two ever disagree.

    `transfer_agent` hands the live call to another Dograh agent by
    `config.workflow_id` (an integer agent id, not a phone number or SIP
    endpoint). Create the destination agent first so its id exists, then the
    tool, then the agent that routes to it. For authenticated HTTP or MCP tools,
    reference an existing `credential_uuid` from `list_credentials`; users
    create credential secrets in the UI, and this flow only stores the UUID
    reference. For MCP tools, the server best-effort discovers the remote
    tool catalog and caches it in `definition.config.discovered_tools`.

    On success, returns `created: true` and the new `tool_uuid`; use that
    UUID in workflow node `tool_uuids`. On failure, returns `created: false`,
    a machine-readable `error_code`, and a human-readable `error`. Possible
    `error_code` values:
    - `validation_error` — the request failed schema validation.
    - `credential_not_found` — a supplied credential_uuid is not in this
      organization; ask the user to create/select it in the UI first.
    - `destination_not_found` — a `transfer_agent` tool's `workflow_id` is
      not an agent in this organization; confirm the id with `list_workflows`.
    - `organization_required` — the API key user has no selected organization.
    - `create_failed` — unexpected persistence or backend failure; retry once,
      then surface the error.
    """
    user = await authenticate_mcp_request()

    try:
        parsed_request = CreateToolRequest.model_validate(request)
    except PydanticValidationError as e:
        return _error_result("validation_error", str(e))

    try:
        tool = await create_tool_for_user(parsed_request, user, source="mcp")
    except ToolManagementError as e:
        return _error_result(e.error_code, e.message)
    except Exception as e:  # noqa: BLE001
        return _error_result("create_failed", str(e))

    return {
        "created": True,
        "tool_uuid": tool.tool_uuid,
        "name": tool.name,
        "category": tool.category,
        "status": tool.status,
        "definition": tool.definition,
    }
