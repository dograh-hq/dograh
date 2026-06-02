from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.openapi.utils import get_openapi

from api.app import app
from api.mcp_server.server import mcp
from api.mcp_server.tools.tool_creation import create_tool
from api.schemas.tool import CreateToolRequest


@pytest.fixture
def authed_user() -> MagicMock:
    user = MagicMock()
    user.id = 11
    user.provider_id = "provider-11"
    user.selected_organization_id = 22
    return user


def _tool_model(**overrides):
    now = datetime.now(UTC)
    values = {
        "id": 3,
        "tool_uuid": "tool-uuid-3",
        "name": "Lookup Account",
        "description": "Lookup an account by phone number",
        "category": "http_api",
        "icon": "globe",
        "icon_color": "#3B82F6",
        "status": "active",
        "definition": {
            "schema_version": 1,
            "type": "http_api",
            "config": {"method": "POST", "url": "https://api.example.com/lookup"},
        },
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _http_tool_request(**config_overrides) -> CreateToolRequest:
    config = {"method": "post", "url": "https://api.example.com/lookup"}
    config.update(config_overrides)
    return CreateToolRequest(
        name="Lookup Account",
        description="Lookup an account by phone number",
        definition={
            "schema_version": 1,
            "type": "http_api",
            "config": config,
        },
    )


@pytest.mark.asyncio
async def test_mcp_create_tool_creates_reusable_tool(authed_user: MagicMock):
    create_tool_mock = AsyncMock(return_value=_tool_model())

    with (
        patch(
            "api.mcp_server.tools.tool_creation.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.services.tool_management.db_client.create_tool",
            create_tool_mock,
        ),
        patch("api.services.tool_management.capture_event") as capture_event_mock,
    ):
        result = await create_tool(_http_tool_request())

    assert result["created"] is True
    assert result["tool_uuid"] == "tool-uuid-3"
    assert result["category"] == "http_api"
    create_tool_mock.assert_awaited_once()
    assert create_tool_mock.call_args.kwargs["organization_id"] == 22
    assert create_tool_mock.call_args.kwargs["user_id"] == 11
    assert create_tool_mock.call_args.kwargs["definition"]["config"]["method"] == "POST"
    capture_event_mock.assert_called_once()
    assert capture_event_mock.call_args.kwargs["properties"]["source"] == "mcp"


@pytest.mark.asyncio
async def test_mcp_create_tool_rejects_unknown_credential(authed_user: MagicMock):
    create_tool_mock = AsyncMock()

    with (
        patch(
            "api.mcp_server.tools.tool_creation.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.services.tool_management.db_client.get_credential_by_uuid",
            AsyncMock(return_value=None),
        ),
        patch(
            "api.services.tool_management.db_client.create_tool",
            create_tool_mock,
        ),
    ):
        result = await create_tool(_http_tool_request(credential_uuid="cred-missing"))

    assert result["created"] is False
    assert result["error_code"] == "credential_not_found"
    create_tool_mock.assert_not_awaited()


def test_sdk_openapi_exposes_create_tool_schema_and_llm_hints():
    sdk_routes = [
        r
        for r in app.routes
        if getattr(r, "openapi_extra", None)
        and "x-sdk-method" in (r.openapi_extra or {})
    ]
    spec = get_openapi(title=app.title, version=app.version, routes=sdk_routes)
    operations = [
        op
        for path_item in spec["paths"].values()
        for op in path_item.values()
        if isinstance(op, dict)
    ]
    assert any(op.get("x-sdk-method") == "create_tool" for op in operations)

    credential_schema = spec["components"]["schemas"]["HttpApiConfig"]["properties"][
        "credential_uuid"
    ]
    assert "list_credentials" in credential_schema["llm_hint"]


@pytest.mark.asyncio
async def test_mcp_create_tool_schema_includes_validation_and_llm_hints():
    tools = await mcp.list_tools()
    create_tool_spec = next(t for t in tools if t.name == "create_tool")

    request_schema = create_tool_spec.parameters["properties"]["request"]
    definition_schema = request_schema["properties"]["definition"]
    http_config = definition_schema["oneOf"][0]["properties"]["config"]

    assert request_schema["properties"]["category"]["enum"] == [
        "http_api",
        "end_call",
        "transfer_call",
        "calculator",
        "native",
        "integration",
        "mcp",
    ]
    assert http_config["properties"]["method"]["enum"] == [
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    ]
    assert (
        "list_credentials" in http_config["properties"]["credential_uuid"]["llm_hint"]
    )
