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
from api.mcp.auth import authenticate_mcp_request
from api.mcp.server import mcp
from api.mcp.tracing import traced_tool
from api.mcp.ts_bridge import TsBridgeError, generate_code


def _pick_workflow_json(workflow: Any) -> dict[str, Any]:
    """Return the latest editable definition for the LLM.

    Draft wins over published — editing a draft is the normal flow.
    Falls back to the legacy `workflow.workflow_definition` column when
    a workflow predates the versioning split.
    """
    current = workflow.current_definition
    if current is not None and current.workflow_json:
        return current.workflow_json
    released = workflow.released_definition
    if released is not None and released.workflow_json:
        return released.workflow_json
    return workflow.workflow_definition or {}


@mcp.tool
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

    payload = _pick_workflow_json(workflow)
    source = (
        "draft"
        if (
            workflow.current_definition
            and workflow.current_definition.status == "draft"
        )
        else ("published" if workflow.released_definition else "legacy")
    )

    try:
        code = await generate_code(payload, workflow_name=workflow.name or "")
    except TsBridgeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate code: {e}")

    return {
        "workflow_id": workflow_id,
        "name": workflow.name or "",
        "version": source,
        "code": code,
    }
