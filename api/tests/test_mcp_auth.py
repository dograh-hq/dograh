from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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
