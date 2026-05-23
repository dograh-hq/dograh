from fastapi import HTTPException

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tools._workflow_projection import project_workflow_to_sdk_view
from api.mcp_server.tracing import traced_tool
from api.mcp_server.ts_bridge import TsBridgeError


@traced_tool
async def list_workflows(status: str | None = "active") -> list[dict]:
    """List agents (workflows) in the caller's organization.

    Returns id, name, status, and created_at for each agent. Use
    `get_workflow` to fetch a single agent's current SDK view and
    metadata. Defaults to active agents; pass `status="archived"` to
    list archived agents, or `status=None` to list all.
    """
    user = await authenticate_mcp_request()
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=user.selected_organization_id,
        status=status,
    )
    return [
        {
            "id": w.id,
            "name": w.name,
            "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in workflows
    ]


@traced_tool
async def get_workflow(workflow_id: int) -> dict:
    """Fetch a single agent by id, projected into the SDK code view.

    Output shape:
        {"id": int, "name": str, "status": str, "version": "draft" | "published" | "legacy", "version_number": int | None, "code": "<TS source>"}
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
        "id": workflow.id,
        "name": view["name"],
        "status": workflow.status,
        "version": view["version"],
        "version_number": view["version_number"],
        "code": view["code"],
    }
