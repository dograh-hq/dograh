"""Marketplace tool REST routes."""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from api.services.auth.depends import get_user
from api.services.tool_marketplace import (
    get_catalog,
    get_marketplace_tool,
    install_marketplace_tool,
)

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
async def oauth_callback(tool_id: int, request: OAuthCallbackRequest, user=Depends(get_user)):
    """Handle OAuth callback after user authorizes the tool."""
    # Validate the user has access to the specified organization
    if request.organization_id != user.selected_organization_id:
        raise HTTPException(status_code=403, detail="Organization access denied")
    
    # Exchange code for token and complete installation.
    # This is implemented in a follow-up task (Task 6: OAuth flow).
    raise HTTPException(status_code=501, detail="OAuth flow not yet implemented")
