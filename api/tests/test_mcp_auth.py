from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from fastapi import HTTPException
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from api.app import app, mcp_app
from api.mcp_server.auth import authenticate_mcp_request


@pytest.mark.asyncio
async def test_authenticate_mcp_request_accepts_bearer_authorization():
    user = MagicMock()
    user.id = 1
    user.selected_organization_id = 90

    with (
        patch(
            "api.mcp_server.auth.get_http_headers",
            return_value={"authorization": "Bearer secret-api-key"},
        ) as get_headers,
        patch(
            "api.mcp_server.auth._handle_api_key_auth",
            AsyncMock(return_value=user),
        ) as handle_auth,
    ):
        authed = await authenticate_mcp_request()

    assert authed is user
    get_headers.assert_called_once_with(include={"authorization"})
    handle_auth.assert_awaited_once_with("secret-api-key")


@pytest.mark.asyncio
async def test_authenticate_mcp_request_accepts_x_api_key():
    user = MagicMock()
    user.id = 2
    user.selected_organization_id = 91

    with (
        patch(
            "api.mcp_server.auth.get_http_headers",
            return_value={"x-api-key": "secret-api-key"},
        ) as get_headers,
        patch(
            "api.mcp_server.auth._handle_api_key_auth",
            AsyncMock(return_value=user),
        ) as handle_auth,
    ):
        authed = await authenticate_mcp_request()

    assert authed is user
    get_headers.assert_called_once_with(include={"authorization"})
    handle_auth.assert_awaited_once_with("secret-api-key")


@pytest.mark.asyncio
async def test_authenticate_mcp_request_rejects_missing_api_key():
    with patch("api.mcp_server.auth.get_http_headers", return_value={}) as get_headers:
        with pytest.raises(HTTPException) as exc_info:
            await authenticate_mcp_request()

    assert exc_info.value.status_code == 401
    assert "Missing API key" in str(exc_info.value.detail)
    get_headers.assert_called_once_with(include={"authorization"})


@asynccontextmanager
async def _mounted_mcp_client(headers):
    def http_client(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

    transport = StreamableHttpTransport(
        "http://testserver/api/v1/mcp/",
        headers=headers,
        httpx_client_factory=http_client,
    )
    async with mcp_app.lifespan(app), Client(transport) as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer secret-api-key"},
        {"X-API-Key": "secret-api-key"},
    ],
)
async def test_mounted_mcp_endpoint_authenticates_tool_calls(headers, monkeypatch):
    handle_auth = AsyncMock(
        return_value=SimpleNamespace(id=1, selected_organization_id=90)
    )
    monkeypatch.setattr("api.mcp_server.auth._handle_api_key_auth", handle_auth)

    async with _mounted_mcp_client(headers) as client:
        tools = await client.list_tools()
        assert "list_node_types" in {tool.name for tool in tools}
        result = await client.call_tool("list_node_types", {})

    assert result.is_error is False
    assert result.data["node_types"]
    handle_auth.assert_awaited_once_with("secret-api-key")


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid-key"}])
async def test_mounted_mcp_endpoint_rejects_unauthenticated_tool_calls(
    headers, monkeypatch
):
    handle_auth = AsyncMock(side_effect=HTTPException(401, "Invalid API key"))
    monkeypatch.setattr("api.mcp_server.auth._handle_api_key_auth", handle_auth)

    async with _mounted_mcp_client(headers) as client:
        result = await client.call_tool("list_node_types", {}, raise_on_error=False)

    assert result.is_error is True
    assert result.data is None
    if headers:
        handle_auth.assert_awaited_once_with("invalid-key")
    else:
        handle_auth.assert_not_awaited()
