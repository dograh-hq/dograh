import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from api.services.tool_marketplace import get_catalog, get_marketplace_tool, install_marketplace_tool

_MARKETPLACE_ROW = MagicMock()
_MARKETPLACE_ROW.id = 1
_MARKETPLACE_ROW.name = "serper_search"
_MARKETPLACE_ROW.display_name = "Serper"
_MARKETPLACE_ROW.category = "mcp_direct"
_MARKETPLACE_ROW.subcategory = "Search"
_MARKETPLACE_ROW.icon = "🔍"
_MARKETPLACE_ROW.description = "Search"
_MARKETPLACE_ROW.tool_category = "mcp"
_MARKETPLACE_ROW.config_template = {"url": "https://example.com", "transport": "streamable_http"}
_MARKETPLACE_ROW.oauth_enabled = False
_MARKETPLACE_ROW.is_active = True

class TestGetCatalog:
    @pytest.mark.asyncio
    async def test_returns_all_active_tools(self):
        with patch("api.services.tool_marketplace.async_session") as mock_sess:
            session = AsyncMock()
            mock_sess.return_value.__aenter__.return_value = session
            mock_result = MagicMock()
            mock_result.fetchall.return_value = [_MARKETPLACE_ROW]
            session.execute.return_value = mock_result
            result = await get_catalog(org_id="test-org")
            assert len(result) == 1
            assert result[0]["name"] == "serper_search"

class TestInstallMarketplaceTool:
    @pytest.mark.asyncio
    async def test_installs_non_oauth_tool(self):
        with patch("api.services.tool_marketplace.async_session") as mock_sess, \
             patch("api.services.tool_marketplace.discover_mcp_tools") as mock_discover:
            session = AsyncMock()
            mock_sess.return_value.__aenter__.return_value = session
            
            # Mock the result objects  
            result1 = MagicMock()
            result1.fetchone.return_value = _MARKETPLACE_ROW
            result2 = MagicMock() 
            result2.fetchone.return_value = None
            
            session.execute.return_value = result1
            session.execute.side_effect = [result1, result2, MagicMock()]  # marketplace tool, then not installed check, then insert
            mock_discover.return_value = [{"name": "search", "description": "Google"}]
            
            result = await install_marketplace_tool(tool_id=1, org_id="test-org")
            assert result["status"] == "active"
            assert "tool_uuid" in result

    @pytest.mark.asyncio
    async def test_rejects_private_url(self):
        with patch("api.services.tool_marketplace.async_session") as mock_sess, \
             patch("api.services.tool_marketplace.validate_public_url") as mock_val:
            session = AsyncMock()
            mock_sess.return_value.__aenter__.return_value = session
            
            result1 = MagicMock()
            result1.fetchone.return_value = _MARKETPLACE_ROW
            result2 = MagicMock()
            result2.fetchone.return_value = None
            
            session.execute.side_effect = [result1, result2]
            mock_val.side_effect = ValueError("private or reserved")
            with pytest.raises(ValueError, match="private"):
                await install_marketplace_tool(tool_id=1, org_id="test-org", user_url="http://localhost/mcp")

    @pytest.mark.asyncio
    async def test_oauth_tool_returns_redirect(self):
        oauth_row = MagicMock()
        oauth_row.id = 2
        oauth_row.name = "hubspot"
        oauth_row.oauth_enabled = True
        oauth_row.oauth_auth_url = "https://app.hubspot.com/oauth/authorize"
        oauth_row.oauth_client_id_env = "HUBSPOT_CLIENT_ID"
        oauth_row.tool_category = "mcp"
        oauth_row.config_template = {"url": "https://mcp.hubspot.com/v1", "transport": "streamable_http"}
        oauth_row.display_name = "HubSpot"
        oauth_row.category = "mcp_direct"
        oauth_row.description = "CRM"

        with patch("api.services.tool_marketplace.async_session") as mock_sess, \
             patch("api.services.tool_marketplace.os.environ.get") as mock_env:
            session = AsyncMock()
            mock_sess.return_value.__aenter__.return_value = session
            
            result1 = MagicMock()
            result1.fetchone.return_value = oauth_row
            result2 = MagicMock()
            result2.fetchone.return_value = None
            
            session.execute.side_effect = [result1, result2]  # mkt tool found, not installed
            mock_env.return_value = "test_client_id"
            result = await install_marketplace_tool(tool_id=2, org_id="test-org")
            assert result["status"] == "oauth_required"
            assert "redirect_url" in result