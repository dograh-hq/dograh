"""Marketplace tool REST routes."""

from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel

from api.services.auth.depends import get_user
from api.services.tool_marketplace import (
    get_catalog,
    get_marketplace_tool,
    install_marketplace_tool,
    complete_oauth_install,
)
from api.services.workflow.mcp_tool_session import discover_mcp_tools
from api.utils.url_validation import validate_public_url

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class ConnectRequest(BaseModel):
    user_url: str | None = None


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str
    organization_id: str


@router.get("/tools")
async def list_tools(category: str | None = Query(default=None), user=Depends(get_user)):
    """List all available marketplace tools, optionally filtered by category."""
    catalog = await get_catalog(
        org_id=user.selected_organization_id,  # Use auth user's org_id for security
        category=category,
    )
    return catalog


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: int, user=Depends(get_user)):
    """Get a single marketplace tool by ID."""
    tool = await get_marketplace_tool(tool_id, org_id=user.selected_organization_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Marketplace tool not found")
    return tool


@router.post("/tools/{tool_id}/connect", status_code=201)
async def connect_tool(tool_id: int, request: ConnectRequest, user=Depends(get_user)):
    """Install a marketplace tool for an organization."""
    try:
        result = await install_marketplace_tool(
            tool_id=tool_id,
            org_id=user.selected_organization_id,  # Use auth user's org_id for security
            user_url=request.user_url,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result.get("status") == "already_installed":
        raise HTTPException(
            status_code=409,
            detail=f"Tool already installed: {result.get('tool_uuid')}",
        )

    if result.get("status") == "oauth_required":
        # Return 200 with oauth_required status so the UI can redirect
        return result

    return result


@router.post("/tools/{tool_id}/oauth/callback")
async def oauth_callback(tool_id: int, oauth_req: OAuthCallbackRequest, user=Depends(get_user), req: Request = None):
    """Handle OAuth callback after user authorizes the tool."""
    if oauth_req.organization_id != user.selected_organization_id:
        raise HTTPException(status_code=403, detail="Organization access denied")
    
    base_url = f"{req.url.scheme}://{req.url.netloc}" if req else "http://localhost:3000"
    
    try:
        result = await complete_oauth_install(
            tool_id=tool_id,
            org_id=oauth_req.organization_id,
            code=oauth_req.code,
            created_by=user.id,
            base_url=base_url,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/test-mcp")
async def test_mcp_connection(url: str = Query(...), user=Depends(get_user)):
    """Test an MCP server connection and return discovered tools."""
    try:
        await validate_public_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        tools = await discover_mcp_tools(
            url=url,
            credential=None,
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
        return {
            "ok": True,
            "url": url,
            "tool_count": len(tools),
            "tools": [{"name": t["name"], "description": t.get("description", "")} for t in tools],
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MCP connection failed: {e}")
