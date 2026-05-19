"""Route-level tests for the MCP tool definition schema.

These tests exercise the Pydantic request models (CreateToolRequest /
UpdateToolRequest) to catch schema gaps at the route/request-model layer —
the layer where the pre-fix defect lived (HTTP 422 on every MCP tool
creation attempt).

Test coverage:
- CreateToolRequest validates a valid MCP definition (was 422 before Part A).
- UpdateToolRequest validates a valid MCP definition.
- Invalid MCP bodies are rejected (ftp:// url, missing url).
- Round-trip: validated definition dict passes through validate_mcp_definition
  unchanged, proving the request schema and call-time validator agree.
- Full HTTP round-trip via the ASGI test client (POST /api/v1/tools/).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.routes.tool import CreateToolRequest, McpToolDefinition, UpdateToolRequest
from api.services.workflow.tools.mcp_tool import (
    validate_mcp_definition,
)

# ── Canonical valid MCP request body ─────────────────────────────────────────

VALID_MCP_DEFINITION = {
    "schema_version": 1,
    "type": "mcp",
    "config": {
        "transport": "streamable_http",
        "url": "https://x/mcp",
        "credential_uuid": None,
        "tools_filter": [],
    },
}


# ── Part A regression: CreateToolRequest / UpdateToolRequest validation ───────


def test_create_tool_request_accepts_mcp_definition():
    """CreateToolRequest must accept an MCP definition (was HTTP 422 before fix)."""
    req = CreateToolRequest(
        name="My MCP Tool",
        description="Integration via MCP",
        category="mcp",
        definition=VALID_MCP_DEFINITION,
    )
    assert isinstance(req.definition, McpToolDefinition)
    assert req.definition.type == "mcp"
    assert req.definition.config.url == "https://x/mcp"
    assert req.definition.config.transport == "streamable_http"
    assert req.definition.config.credential_uuid is None
    assert req.definition.config.tools_filter == []
    assert req.definition.config.timeout_secs == 30
    assert req.definition.config.sse_read_timeout_secs == 300


def test_update_tool_request_accepts_mcp_definition():
    """UpdateToolRequest must also accept an MCP definition."""
    req = UpdateToolRequest(
        name="Updated MCP Tool",
        definition=VALID_MCP_DEFINITION,
    )
    assert isinstance(req.definition, McpToolDefinition)
    assert req.definition.type == "mcp"
    assert req.definition.config.url == "https://x/mcp"


def test_create_tool_request_accepts_mcp_with_all_fields():
    """All optional MCP config fields are accepted and preserved."""
    req = CreateToolRequest(
        name="Full MCP Tool",
        category="mcp",
        definition={
            "schema_version": 1,
            "type": "mcp",
            "config": {
                "transport": "streamable_http",
                "url": "https://acme.example.com/mcp",
                "credential_uuid": "cred-abc-123",
                "tools_filter": ["lookup_patient", "schedule_appointment"],
                "timeout_secs": 60,
                "sse_read_timeout_secs": 600,
            },
        },
    )
    cfg = req.definition.config  # type: ignore[union-attr]
    assert cfg.url == "https://acme.example.com/mcp"
    assert cfg.credential_uuid == "cred-abc-123"
    assert cfg.tools_filter == ["lookup_patient", "schedule_appointment"]
    assert cfg.timeout_secs == 60
    assert cfg.sse_read_timeout_secs == 600


# ── Invalid bodies are rejected ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "definition",
    [
        # ftp:// URL — rejected by McpToolConfig.validate_url
        {
            "schema_version": 1,
            "type": "mcp",
            "config": {"transport": "streamable_http", "url": "ftp://x/mcp"},
        },
        # Empty url — rejected by McpToolConfig.validate_url
        {
            "schema_version": 1,
            "type": "mcp",
            "config": {"transport": "streamable_http", "url": ""},
        },
        # Missing url — rejected by McpToolConfig (required field)
        {
            "schema_version": 1,
            "type": "mcp",
            "config": {"transport": "streamable_http"},
        },
        # Unsupported transport — rejected because Literal["streamable_http"] constraint
        {
            "schema_version": 1,
            "type": "mcp",
            "config": {"url": "https://x/mcp", "transport": "stdio"},
        },
    ],
)
def test_create_tool_request_rejects_invalid_mcp_definition(definition):
    """Invalid MCP definitions must raise ValidationError."""
    with pytest.raises(ValidationError):
        CreateToolRequest(
            name="Bad MCP Tool",
            category="mcp",
            definition=definition,
        )


# ── Round-trip compatibility: request schema ↔ validate_mcp_definition ───────


def test_mcp_definition_round_trips_through_validate_mcp_definition():
    """The dict produced by CreateToolRequest.definition.model_dump() must be
    accepted by validate_mcp_definition without raising, and the result must
    contain the expected fields.  This proves the request-layer schema and the
    call-time validator agree on the stored config shape."""
    req = CreateToolRequest(
        name="Round-Trip MCP Tool",
        category="mcp",
        definition={
            "schema_version": 1,
            "type": "mcp",
            "config": {
                "transport": "streamable_http",
                "url": "https://roundtrip.example.com/mcp",
                "credential_uuid": "cred-rt-456",
                "tools_filter": ["ping"],
                "timeout_secs": 45,
                "sse_read_timeout_secs": 400,
            },
        },
    )

    # Simulate what the route does: persist definition as a plain dict
    persisted = req.definition.model_dump()  # type: ignore[union-attr]

    # validate_mcp_definition must accept the persisted shape without raising
    normalized = validate_mcp_definition(persisted)

    assert normalized["url"] == "https://roundtrip.example.com/mcp"
    assert normalized["transport"] == "streamable_http"
    assert normalized["credential_uuid"] == "cred-rt-456"
    assert normalized["tools_filter"] == ["ping"]
    assert normalized["timeout_secs"] == 45
    assert normalized["sse_read_timeout_secs"] == 400


def test_mcp_definition_round_trip_defaults():
    """Round-trip with minimal body: defaults fill in correctly and
    validate_mcp_definition agrees on them."""
    req = CreateToolRequest(
        name="Minimal MCP Tool",
        category="mcp",
        definition=VALID_MCP_DEFINITION,
    )

    persisted = req.definition.model_dump()  # type: ignore[union-attr]
    normalized = validate_mcp_definition(persisted)

    assert normalized["transport"] == "streamable_http"
    assert normalized["tools_filter"] == []
    assert normalized["timeout_secs"] == 30
    assert normalized["sse_read_timeout_secs"] == 300
    assert normalized["credential_uuid"] is None
    # Part B: auth_header / auth_scheme must NOT be present in the normalized
    # config dict (they were dead config removed in the fix)
    assert "auth_header" not in normalized
    assert "auth_scheme" not in normalized


# ── Full HTTP round-trip via ASGI test client ─────────────────────────────────


async def test_post_tool_mcp_returns_200(test_client_factory, db_session):
    """POST /api/v1/tools/ with an MCP definition must return HTTP 200 and
    persist the definition with type='mcp'.  Before Part A this always
    returned 422."""
    # Create a user and an organization, then link them so the route's
    # selected_organization_id check passes.
    user, _ = await db_session.get_or_create_user_by_provider_id("mcp_route_test_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        "mcp_route_test_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    # Reload the user so selected_organization_id is populated on the object.
    user = await db_session.get_user_by_id(user.id)

    async with test_client_factory(user) as client:
        response = await client.post(
            "/api/v1/tools/",
            json={
                "name": "HTTP Round-Trip MCP Tool",
                "description": "Testing the full route",
                "category": "mcp",
                "definition": {
                    "schema_version": 1,
                    "type": "mcp",
                    "config": {
                        "transport": "streamable_http",
                        "url": "https://roundtrip.example.com/mcp",
                        "credential_uuid": None,
                        "tools_filter": [],
                    },
                },
            },
        )

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["definition"]["type"] == "mcp"
    assert body["definition"]["config"]["url"] == "https://roundtrip.example.com/mcp"
    assert body["category"] == "mcp"


async def test_post_tool_mcp_invalid_url_returns_422(test_client_factory, db_session):
    """POST /api/v1/tools/ with an ftp:// URL must return HTTP 422."""
    user, _ = await db_session.get_or_create_user_by_provider_id(
        "mcp_route_test_user_422"
    )
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        "mcp_route_test_org_422", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    user = await db_session.get_user_by_id(user.id)

    async with test_client_factory(user) as client:
        response = await client.post(
            "/api/v1/tools/",
            json={
                "name": "Bad MCP Tool",
                "category": "mcp",
                "definition": {
                    "schema_version": 1,
                    "type": "mcp",
                    "config": {
                        "transport": "streamable_http",
                        "url": "ftp://invalid.example.com/mcp",
                    },
                },
            },
        )

    assert response.status_code == 422


# ── Task 6: discovered_tools field and _populate_discovered_tools helper ──────

from unittest.mock import AsyncMock, MagicMock

from api.routes.tool import McpToolConfig, _populate_discovered_tools


def test_mcp_config_accepts_discovered_tools():
    cfg = McpToolConfig(
        url="https://x/mcp",
        discovered_tools=[{"name": "echo", "description": "Echo"}],
    )
    assert cfg.discovered_tools == [{"name": "echo", "description": "Echo"}]
    # Defaults to [] when omitted
    assert McpToolConfig(url="https://x/mcp").discovered_tools == []


@pytest.mark.asyncio
async def test_populate_discovered_tools_overwrites_cache(monkeypatch):
    import api.routes.tool as tool_mod

    monkeypatch.setattr(
        tool_mod,
        "discover_mcp_tools",
        AsyncMock(return_value=[{"name": "echo", "description": "Echo"}]),
    )
    definition = {
        "schema_version": 1,
        "type": "mcp",
        "config": {
            "url": "https://x/mcp",
            "tools_filter": [],
            "discovered_tools": [{"name": "stale", "description": "old"}],
        },
    }
    out = await _populate_discovered_tools(definition, organization_id=1)
    assert out["config"]["discovered_tools"] == [
        {"name": "echo", "description": "Echo"}
    ]


@pytest.mark.asyncio
async def test_populate_discovered_tools_non_mcp_is_noop():
    definition = {"schema_version": 1, "type": "http_api", "config": {}}
    out = await _populate_discovered_tools(definition, organization_id=1)
    assert out == definition  # untouched


@pytest.mark.asyncio
async def test_populate_discovered_tools_server_down_sets_empty(monkeypatch):
    import api.routes.tool as tool_mod

    monkeypatch.setattr(
        tool_mod,
        "discover_mcp_tools",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    )
    definition = {
        "schema_version": 1,
        "type": "mcp",
        "config": {"url": "https://x/mcp", "tools_filter": []},
    }
    out = await _populate_discovered_tools(definition, organization_id=1)
    assert out["config"]["discovered_tools"] == []


# ── Task 7: POST /{tool_uuid}/mcp/refresh ─────────────────────────────────────

from fastapi import HTTPException

from api.routes.tool import refresh_mcp_tools


def _fake_user(org_id=1):
    u = MagicMock()
    u.selected_organization_id = org_id
    u.id = 1
    u.provider_id = "p1"
    return u


def _mcp_tool_model(org_id=1):
    t = MagicMock()
    t.tool_uuid = "tu-mcp"
    t.name = "Mock MCP"
    t.category = "mcp"
    t.definition = {
        "schema_version": 1,
        "type": "mcp",
        "config": {"url": "https://x/mcp", "tools_filter": []},
    }
    return t


@pytest.mark.asyncio
async def test_refresh_success(monkeypatch):
    import api.routes.tool as tool_mod

    tool = _mcp_tool_model()
    monkeypatch.setattr(
        tool_mod.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_mod.db_client,
        "update_tool",
        AsyncMock(return_value=tool),
    )
    monkeypatch.setattr(
        tool_mod,
        "discover_mcp_tools",
        AsyncMock(return_value=[{"name": "echo", "description": "Echo"}]),
    )
    resp = await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert resp.discovered_tools == [{"name": "echo", "description": "Echo"}]
    assert resp.error is None


@pytest.mark.asyncio
async def test_refresh_server_down_returns_200_with_error(monkeypatch):
    import api.routes.tool as tool_mod

    tool = _mcp_tool_model()
    monkeypatch.setattr(
        tool_mod.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(tool_mod.db_client, "update_tool", AsyncMock(return_value=tool))
    monkeypatch.setattr(tool_mod, "discover_mcp_tools", AsyncMock(return_value=[]))
    resp = await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert resp.discovered_tools == []
    assert resp.error  # non-empty human-readable message
    # update_tool should NOT be called when discovery returns empty
    tool_mod.db_client.update_tool.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_non_mcp_is_400(monkeypatch):
    import api.routes.tool as tool_mod

    tool = _mcp_tool_model()
    tool.category = "http_api"
    monkeypatch.setattr(
        tool_mod.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    with pytest.raises(HTTPException) as ei:
        await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_refresh_not_found_is_404(monkeypatch):
    import api.routes.tool as tool_mod

    monkeypatch.setattr(
        tool_mod.db_client, "get_tool_by_uuid", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as ei:
        await refresh_mcp_tools("nope", user=_fake_user())
    assert ei.value.status_code == 404
