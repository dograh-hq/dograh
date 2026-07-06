"""Tool marketplace service for discovering and installing tools from the marketplace."""

import os
import uuid
from typing import Dict, List, Optional, Any
from sqlalchemy import text

from api.db.database import async_session
from api.services.workflow.mcp_tool_session import discover_mcp_tools
from api.utils.url_validation import validate_public_url


async def get_catalog(org_id: str) -> List[Dict[str, Any]]:
    """Get all active tools from the marketplace."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, name, display_name, category, subcategory, icon, 
                       description, tool_category, config_template, oauth_enabled, is_active
                FROM tool_marketplace 
                WHERE is_active = true 
                ORDER BY sort_order, display_name
            """)
        )
        rows = result.fetchall()
        return [
            {
                "id": row.id,
                "name": row.name,
                "display_name": row.display_name,
                "category": row.category,
                "subcategory": row.subcategory,
                "icon": row.icon,
                "description": row.description,
                "tool_category": row.tool_category,
                "config_template": row.config_template,
                "oauth_enabled": row.oauth_enabled,
                "is_active": row.is_active,
            }
            for row in rows
        ]


async def get_marketplace_tool(tool_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific marketplace tool by ID."""
    async with async_session() as session:
        result = await session.execute(
            text("SELECT * FROM tool_marketplace WHERE id = :tool_id"),
            {"tool_id": tool_id}
        )
        row = result.fetchone()
        if not row:
            return None
        
        return {
            "id": row.id,
            "name": row.name,
            "display_name": row.display_name,
            "category": row.category,
            "subcategory": row.subcategory,
            "icon": row.icon,
            "description": row.description,
            "tool_category": row.tool_category,
            "config_template": row.config_template,
            "oauth_enabled": row.oauth_enabled,
            "oauth_auth_url": row.oauth_auth_url,
            "oauth_token_url": row.oauth_token_url,
            "oauth_scopes": row.oauth_scopes,
            "oauth_redirect_path": row.oauth_redirect_path,
            "oauth_client_id_env": row.oauth_client_id_env,
            "is_active": row.is_active,
        }


async def install_marketplace_tool(
    tool_id: int, 
    org_id: str, 
    user_url: Optional[str] = None
) -> Dict[str, Any]:
    """Install a marketplace tool for an organization."""
    async with async_session() as session:
        # Get the marketplace tool
        result = await session.execute(
            text("SELECT * FROM tool_marketplace WHERE id = :tool_id"),
            {"tool_id": tool_id}
        )
        marketplace_tool = result.fetchone()
        if not marketplace_tool:
            raise ValueError(f"Marketplace tool {tool_id} not found")
        
        # Check if already installed
        result = await session.execute(
            text("""
                SELECT id FROM tools 
                WHERE organization_id = :org_id 
                AND name = :name
            """),
            {"org_id": org_id, "name": marketplace_tool.name}
        )
        existing = result.fetchone()
        if existing:
            raise ValueError(f"Tool {marketplace_tool.name} already installed")
        
        # For OAuth tools, check if we have credentials
        if marketplace_tool.oauth_enabled:
            client_id = os.environ.get(marketplace_tool.oauth_client_id_env) if marketplace_tool.oauth_client_id_env else None
            if not client_id:
                raise ValueError(f"OAuth client ID not configured for {marketplace_tool.name}")
            
            # For now, return redirect URL (would need actual OAuth flow implementation)
            auth_url = marketplace_tool.oauth_auth_url or "https://example.com/oauth"
            redirect_url = f"{auth_url}?client_id={client_id}&redirect_uri=TODO"
            return {
                "status": "oauth_required",
                "redirect_url": redirect_url
            }
        
        # Validate user-provided URL if present
        tool_url = user_url or marketplace_tool.config_template.get("url")
        if tool_url:
            await validate_public_url(tool_url)
        
        # Generate tool UUID
        tool_uuid = str(uuid.uuid4())
        
        # Insert the tool into the tools table
        await session.execute(
            text("""
                INSERT INTO tools (
                    tool_uuid, organization_id, name, description, category,
                    icon, status, configuration, created_at, updated_at
                ) VALUES (
                    :tool_uuid, :org_id, :name, :description, :category,
                    :icon, 'active', :config, NOW(), NOW()
                )
            """),
            {
                "tool_uuid": tool_uuid,
                "org_id": org_id,
                "name": marketplace_tool.name,
                "description": marketplace_tool.description,
                "category": marketplace_tool.tool_category,
                "icon": marketplace_tool.icon,
                "config": {
                    "url": tool_url,
                    "transport": marketplace_tool.config_template.get("transport", "streamable_http")
                }
            }
        )
        
        await session.commit()
        
        # Discover MCP tools if it's an MCP tool
        if marketplace_tool.tool_category == "mcp" and tool_url:
            try:
                tools = await discover_mcp_tools(
                    url=tool_url,
                    credential=None,
                    timeout_secs=10,
                    sse_read_timeout_secs=30
                )
                # Tools discovered successfully means the MCP server is working
            except Exception:
                # Discovery failed, but tool is still installed
                pass
        
        return {
            "status": "active",
            "tool_uuid": tool_uuid,
            "name": marketplace_tool.name
        }