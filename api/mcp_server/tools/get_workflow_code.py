"""MCP tool that returns a workflow as SDK TypeScript code.

Companion to `save_workflow`: the LLM calls `get_workflow_code` to see
the current state of a workflow as editable code, mutates it, and calls
`save_workflow` with the new code. Storage stays JSON; the TS form is
an ephemeral projection for the LLM edit loop.

Selection priority: latest draft → latest published → legacy
`workflow.workflow_definition`. That matches the UI's "whichever is the
working copy" behavior so the LLM sees what a human editor would see.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tools._workflow_projection import project_workflow_to_sdk_view
from api.mcp_server.tracing import traced_tool
from api.mcp_server.ts_bridge import TsBridgeError


@traced_tool
async def get_workflow_code(workflow_id: int) -> dict[str, Any]:
    """Return the workflow as SDK TypeScript code the LLM can edit.

    Output shape:
        {"code": "<TS source>", "workflow_id": int, "version": "draft" | "published" | "legacy"}

    The LLM edits `code`, then calls `save_workflow(workflow_id, code)`.
    """
    user = await authenticate_mcp_request()

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    try:
        view = await project_workflow_to_sdk_view(workflow)
    except TsBridgeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate code: {e}")

    return {
        "workflow_id": workflow_id,
        "name": view["name"],
        "version": view["version"],
        "code": view["code"],
    }
