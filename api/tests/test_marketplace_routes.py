import pytest
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient
from types import SimpleNamespace
from fastapi import FastAPI
from api.routes.marketplace import router
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    """Create test app with auth mocked."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1,
        provider_id="provider-1",
        selected_organization_id="test-org-id",
    )
    return app


@pytest.fixture
def async_client():
    """Return an async HTTPX client for the FastAPI test app."""
    app = _make_test_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestListMarketplaceTools:
    @pytest.mark.asyncio
    async def test_returns_catalog(self, async_client):
        """GET /api/v1/marketplace/tools should return the catalog."""
        with patch(
            "api.routes.marketplace.get_catalog"
        ) as mock_get_catalog:
            mock_get_catalog.return_value = [
                {
                    "id": 1,
                    "name": "serper_search",
                    "display_name": "Serper Google Search",
                    "category": "mcp_direct",
                    "subcategory": "Search",
                    "icon": "🔍",
                    "description": "Effettua ricerche...",
                    "oauth_enabled": False,
                    "is_installed": False,
                }
            ]

            response = await async_client.get("/api/v1/marketplace/tools")

            # Verify it calls get_catalog with the auth user's org_id
            mock_get_catalog.assert_called_once_with(
                org_id="test-org-id",
                category=None,
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["name"] == "serper_search"

    @pytest.mark.asyncio
    async def test_filters_by_category(self, async_client):
        """GET with ?category=dify_workflow should filter."""
        with patch(
            "api.routes.marketplace.get_catalog"
        ) as mock_get_catalog:
            mock_get_catalog.return_value = []
            response = await async_client.get(
                "/api/v1/marketplace/tools?category=dify_workflow"
            )
            
            # Verify it calls get_catalog with the auth user's org_id and category filter
            mock_get_catalog.assert_called_once_with(
                org_id="test-org-id",
                category="dify_workflow",
            )
            assert response.status_code == 200
            assert response.json() == []


class TestGetMarketplaceTool:
    @pytest.mark.asyncio
    async def test_returns_tool(self, async_client):
        """GET /api/v1/marketplace/tools/{id} should return the tool."""
        with patch(
            "api.routes.marketplace.get_marketplace_tool"
        ) as mock_get:
            mock_get.return_value = {
                "id": 1,
                "name": "serper_search",
                "display_name": "Serper Google Search",
                "category": "mcp_direct",
                "description": "Effettua ricerche...",
                "oauth_enabled": False,
            }
            response = await async_client.get("/api/v1/marketplace/tools/1")
            
            # Verify it calls get_marketplace_tool with the auth user's org_id
            mock_get.assert_called_once_with(1, org_id="test-org-id")
            assert response.status_code == 200
            assert response.json()["name"] == "serper_search"

    @pytest.mark.asyncio
    async def test_404_for_missing(self, async_client):
        """GET with unknown id should return 404."""
        with patch(
            "api.routes.marketplace.get_marketplace_tool"
        ) as mock_get:
            mock_get.return_value = None
            response = await async_client.get("/api/v1/marketplace/tools/999")
            
            # Verify it calls get_marketplace_tool with the auth user's org_id
            mock_get.assert_called_once_with(999, org_id="test-org-id")
            assert response.status_code == 404


class TestConnectMarketplaceTool:
    @pytest.mark.asyncio
    async def test_installs_tool(self, async_client):
        """POST /connect should install the tool."""
        with patch(
            "api.routes.marketplace.install_marketplace_tool"
        ) as mock_install:
            mock_install.return_value = {
                "tool_uuid": "abc-123",
                "status": "active",
                "discovered_tools": [{"name": "search", "description": "..."}],
            }
            response = await async_client.post(
                "/api/v1/marketplace/tools/1/connect",
                json={"user_url": None},
            )
            
            # Verify it calls install with the auth user's org_id
            mock_install.assert_called_once_with(
                tool_id=1,
                org_id="test-org-id",
                user_url=None,
            )
            assert response.status_code == 201
            assert response.json()["tool_uuid"] == "abc-123"

    @pytest.mark.asyncio
    async def test_409_for_already_installed(self, async_client):
        """POST /connect should return 409 if already installed."""
        with patch(
            "api.routes.marketplace.install_marketplace_tool"
        ) as mock_install:
            mock_install.return_value = {
                "status": "already_installed",
                "tool_uuid": "existing-uuid",
            }
            response = await async_client.post(
                "/api/v1/marketplace/tools/1/connect",
                json={"user_url": None},
            )
            
            # Verify it calls install with the auth user's org_id
            mock_install.assert_called_once_with(
                tool_id=1,
                org_id="test-org-id",
                user_url=None,
            )
            assert response.status_code == 409


class TestOAuthCallback:
    @pytest.mark.asyncio
    async def test_callback_completes_install(self, async_client):
        """OAuth callback should complete the pending installation."""
        with (
            patch("api.routes.marketplace.complete_oauth_install") as mock_complete,
        ):
            mock_complete.return_value = {
                "tool_uuid": "abc-456",
                "status": "active",
                "discovered_tools": [{"name": "search_contacts", "description": "..."}],
            }
            response = await async_client.post(
                "/api/v1/marketplace/tools/3/oauth/callback",
                json={
                    "code": "auth_code_123",
                    "state": "3:test-org-id",
                    "organization_id": "test-org-id",
                },
            )
            assert response.status_code == 200
            assert response.json()["tool_uuid"] == "abc-456"
