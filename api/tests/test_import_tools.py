"""Tests for bulk tool import and normalization logic."""

import pytest
from unittest.mock import AsyncMock, patch
from api.db.models import UserModel
from api.schemas.tool import ImportToolsRequest
from api.services.tool_management import (
    normalize_import_tool_data,
    import_tools_for_user,
)


def test_normalize_legacy_exported_tool():
    """Test normalizing a legacy exported tool dict (with flat definition)."""
    raw_item = {
        "id": 1,
        "tool_uuid": "41da0daa-ce00-46f3-a81c-d3ae6181c6e5",
        "name": "Check Order Status",
        "description": "Lookup status of orders by order ID",
        "category": "http_api",
        "icon": "Package",
        "icon_color": "#3B82F6",
        "status": "archived",
        "definition": {
            "name": "check_order_status",
            "description": "Check status of a customer order given an order ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The 6-digit order ID, e.g., ORD123",
                    }
                },
                "required": ["order_id"],
            },
            "url": "https://httpbin.org/post",
            "method": "POST",
            "headers": {"Content-Type": "application/json"},
        },
        "created_at": "2026-08-06T14:08:55.830261+05:30",
    }

    req = normalize_import_tool_data(raw_item)
    assert req.name == "Check Order Status"
    assert req.description == "Lookup status of orders by order ID"
    assert req.category == "http_api"
    assert req.icon == "Package"
    assert req.icon_color == "#3B82F6"
    assert req.definition.type == "http_api"
    assert req.definition.config.url == "https://httpbin.org/post"
    assert req.definition.config.method == "POST"
    assert req.definition.config.headers == {"Content-Type": "application/json"}

    params = req.definition.config.parameters
    assert len(params) == 1
    assert params[0].name == "order_id"
    assert params[0].type == "string"
    assert params[0].required is True


def test_normalize_typed_tool():
    """Test normalizing a typed tool dict with schema_version and config."""
    raw_item = {
        "id": 6,
        "tool_uuid": "cbaa4981-c301-4d4c-aebe-fbb08943c205",
        "name": "check_menu",
        "description": "this tool checks the menu",
        "category": "http_api",
        "icon": "globe",
        "icon_color": "#3B82F6",
        "status": "active",
        "definition": {
            "schema_version": 1,
            "type": "http_api",
            "config": {
                "method": "GET",
                "url": "http://localhost:5678/webhook/menu",
                "headers": None,
                "credential_uuid": None,
                "parameters": None,
                "preset_parameters": None,
                "timeout_ms": 5000,
                "customMessage": None,
                "customMessageType": "audio",
                "customMessageRecordingId": "2",
            },
        },
        "created_at": "2026-08-06T15:24:48.225658+05:30",
    }

    req = normalize_import_tool_data(raw_item)
    assert req.name == "check_menu"
    assert req.description == "this tool checks the menu"
    assert req.definition.config.method == "GET"
    assert req.definition.config.url == "http://localhost:5678/webhook/menu"


from datetime import datetime
from api.schemas.tool import ToolResponse

@pytest.mark.asyncio
async def test_import_tools_for_user_success():
    """Test bulk importing tools for a user with mock db client."""
    user = UserModel(id=10, provider_id="prov_10", selected_organization_id=1)
    prompt_sample = [
        {
            "id": 1,
            "name": "Check Order Status",
            "category": "http_api",
            "definition": {
                "url": "https://httpbin.org/post",
                "method": "POST",
            },
        },
        {
            "id": 6,
            "name": "check_menu",
            "category": "http_api",
            "definition": {
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "http://localhost:5678/webhook/menu",
                },
            },
        },
    ]

    import_req = ImportToolsRequest(tools=prompt_sample)

    with patch("api.services.tool_management.create_tool_for_user") as mock_create:
        mock_tool_1 = ToolResponse(
            id=101,
            tool_uuid="uuid-1",
            name="Check Order Status",
            description=None,
            category="http_api",
            icon="globe",
            icon_color="#3B82F6",
            status="active",
            definition={},
            created_at=datetime.now(),
            updated_at=None,
            created_by=None,
        )

        mock_create.side_effect = [mock_tool_1, mock_tool_1]

        res = await import_tools_for_user(import_req, user)
        assert len(res.imported) == 2
        assert len(res.errors) == 0

