"""MCP tools for browsing and installing marketplace tools."""

from api.services.tool_marketplace import get_catalog, install_marketplace_tool
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool


@traced_tool
async def list_marketplace_tools(
    category: str | None = None,
) -> list[dict]:
    """List all available tools in the Dograh marketplace.

    Use this to discover what third-party integrations are available.

    Args:
        category: Optional filter. One of: "mcp_direct", "dify_workflow", "http_api".
            Omit to list all categories.
    """
    user = await authenticate_mcp_request()
    catalog = await get_catalog(org_id=user.selected_organization_id, category=category)
    # Return a leaner version for the LLM
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "display_name": t["display_name"],
            "category": t["category"],
            "description": t["description"],
            "oauth_enabled": t["oauth_enabled"],
        }
        for t in catalog
    ]


@traced_tool
async def install_marketplace_tool_mcp(
    marketplace_tool_id: int,
    organization_id: str,
    user_url: str | None = None,
) -> dict:
    """Install a marketplace tool for an organization.

    This creates a new tool in the organization's tool library. For MCP tools,
    it also discovers the available functions from the remote server.

    Args:
        marketplace_tool_id: The ID of the marketplace tool to install.
        organization_id: The target organization UUID.
        user_url: For tools that require a user-provided URL (e.g. Dify),
            paste the MCP server URL here.
    """
    user = await authenticate_mcp_request()
    
    # Validate organization_id parameter matches authenticated user
    if organization_id != str(user.selected_organization_id):
        raise ValueError(
            f"organization_id mismatch: caller org {user.selected_organization_id} "
            f"does not match requested org {organization_id}"
        )
    
    result = await install_marketplace_tool(
        tool_id=marketplace_tool_id,
        org_id=organization_id,
        user_url=user_url,
    )
    return result
