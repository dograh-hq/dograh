"""Tool marketplace service for discovering and installing tools from the marketplace."""

import os
import uuid
from typing import Dict, List, Optional, Any
from sqlalchemy import text

from api.db.database import async_session
from api.services.workflow.mcp_tool_session import discover_mcp_tools
from api.utils.url_validation import validate_public_url


async def get_catalog(org_id: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all active tools from the marketplace."""
    async with async_session() as session:
        # Base query
        base_query = """
            SELECT tm.id, tm.name, tm.display_name, tm.category, tm.subcategory, tm.icon, 
                   tm.description, tm.tool_category, tm.config_template, tm.oauth_enabled, tm.is_active,
                   (CASE WHEN t.id IS NOT NULL THEN true ELSE false END) as is_installed
            FROM tool_marketplace tm
            LEFT JOIN tools t ON tm.name = t.name AND t.organization_id = :org_id
            WHERE tm.is_active = true
        """
        
        # Add category filter if provided
        if category:
            query = base_query + " AND tm.category = :category ORDER BY tm.sort_order, tm.display_name"
            params = {"org_id": org_id, "category": category}
        else:
            query = base_query + " ORDER BY tm.sort_order, tm.display_name"
            params = {"org_id": org_id}
        
        result = await session.execute(text(query), params)
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
                "is_installed": row.is_installed,
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
        marketplace_result = await session.execute(
            text("SELECT * FROM tool_marketplace WHERE id = :tool_id"),
            {"tool_id": tool_id}
        )
        marketplace_tool = marketplace_result.fetchone()
        if not marketplace_tool:
            raise ValueError(f"Marketplace tool {tool_id} not found")
        
        # Check if already installed
        existing_result = await session.execute(
            text("""
                SELECT id FROM tools 
                WHERE organization_id = :org_id 
                AND name = :name
            """),
            {"org_id": org_id, "name": marketplace_tool.name}
        )
        existing = existing_result.fetchone()
        if existing:
            raise ValueError(f"Tool {marketplace_tool.name} already installed")
        
        # For OAuth tools, check if we have credentials
        if marketplace_tool.oauth_enabled:
            client_id = os.environ.get(marketplace_tool.oauth_client_id_env) if marketplace_tool.oauth_client_id_env else None
            if not client_id:
                raise ValueError(f"OAuth client ID not configured for {marketplace_tool.name}")
            
            # Construct redirect URI from marketplace configuration
            auth_url = marketplace_tool.oauth_auth_url or "https://example.com/oauth"
            redirect_path = marketplace_tool.oauth_redirect_path or "/oauth/callback"
            # TODO: Get base URL from app config
            base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")
            redirect_uri = f"{base_url}{redirect_path}"
            
            redirect_url = f"{auth_url}?client_id={client_id}&redirect_uri={redirect_uri}"
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
                    icon, status, definition, created_by, created_at, updated_at
                ) VALUES (
                    :tool_uuid, :org_id, :name, :description, :category,
                    :icon, :status, :definition, :created_by, NOW(), NOW()
                )
            """),
            {
                "tool_uuid": tool_uuid,
                "org_id": org_id,
                "name": marketplace_tool.name,
                "description": marketplace_tool.description,
                "category": marketplace_tool.tool_category,
                "icon": marketplace_tool.icon,
                "status": "active",
                "definition": {
                    "schema_version": 1,
                    "type": marketplace_tool.tool_category,
                    "config": {
                        "url": tool_url,
                        "transport": marketplace_tool.config_template.get("transport", "streamable_http")
                    }
                },
                "created_by": 1  # TODO: Get actual user ID from context
            }
        )
        
        await session.commit()
        
        # Discover MCP tools if it's an MCP tool
        discovered_tools = []
        if marketplace_tool.tool_category == "mcp" and tool_url:
            try:
                # Use timeouts from config_template with fallbacks
                timeout_secs = marketplace_tool.config_template.get("timeout_secs", 10)
                sse_read_timeout_secs = marketplace_tool.config_template.get("sse_read_timeout_secs", 30)
                
                discovered_tools = await discover_mcp_tools(
                    url=tool_url,
                    credential=None,
                    timeout_secs=timeout_secs,
                    sse_read_timeout_secs=sse_read_timeout_secs
                )
                # Tools discovered successfully means the MCP server is working
            except Exception as e:
                logger.warning(
                    f"MCP discovery failed for '{marketplace_tool.name}' "
                    f"at {tool_url}: {e}"
                )
        
        return {
            "status": "active",
            "tool_uuid": tool_uuid,
            "name": marketplace_tool.name,
            "discovered_tools": discovered_tools
        }