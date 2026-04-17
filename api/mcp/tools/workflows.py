from fastapi import HTTPException

from api.db import db_client
from api.mcp.auth import authenticate_mcp_request
from api.mcp.server import mcp


@mcp.tool
async def list_workflows(status: str | None = None) -> list[dict]:
    """List agents (workflows) in the caller's organization.

    Returns id, name, status, and created_at for each agent. Use
    `get_workflow` to fetch a single agent's full definition. Pass
    `status="active"` or `status="archived"` to filter.
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


@mcp.tool
async def get_workflow(workflow_id: int) -> dict:
    """Fetch a single agent by id, including its current published definition."""
    user = await authenticate_mcp_request()
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    current = workflow.current_definition
    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "definition": current.workflow_json if current else None,
        "version_number": current.version_number if current else None,
    }
