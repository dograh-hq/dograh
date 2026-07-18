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

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.routes.tool import (
    CreateToolRequest,
    McpToolConfig,
    McpToolDefinition,
    ToolTestRequest,
    ToolTestResponse,
    UpdateToolRequest,
    _populate_discovered_tools,
    refresh_mcp_tools,
    router,
)
from api.routes.tool import (
    test_tool as call_test_tool_route,
)
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


def test_update_tool_request_accepts_http_api_complex_parameter_types():
    """HTTP API tools may accept structured JSON parameters."""
    req = UpdateToolRequest(
        name="Check Availability New Multi",
        description="Check Availability when asked for it.",
        definition={
            "schema_version": 1,
            "type": "http_api",
            "config": {
                "method": "POST",
                "url": "https://automation.dograh.com/webhook/example",
                "parameters": [
                    {
                        "name": "params",
                        "type": "object",
                        "description": (
                            "An object containing the name and datetime in ISO format"
                        ),
                        "required": True,
                    },
                    {
                        "name": "slots",
                        "type": "array",
                        "description": "Candidate availability slots.",
                        "required": False,
                    },
                ],
                "preset_parameters": [
                    {
                        "name": "phone_number",
                        "type": "string",
                        "value_template": "{{initial_context.phone_number}}",
                        "required": True,
                    }
                ],
                "timeout_ms": 5000,
                "customMessageType": "text",
            },
        },
    )

    assert req.definition.type == "http_api"
    parameters = req.definition.config.parameters
    assert parameters[0].type == "object"
    assert parameters[1].type == "array"


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
    import api.services.tool_management as tool_svc

    monkeypatch.setattr(
        tool_svc,
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
    import api.services.tool_management as tool_svc

    monkeypatch.setattr(
        tool_svc,
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


def _http_tool_model(method="GET"):
    t = MagicMock()
    t.tool_uuid = "tu-http"
    t.name = "Mock HTTP"
    t.category = "http_api"
    t.definition = {
        "schema_version": 1,
        "type": "http_api",
        "config": {"method": method, "url": "https://example.com/search"},
    }
    return t


@pytest.mark.asyncio
async def test_tool_executes_http_api_tool_with_llm_and_preset_params(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model()
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    executor = AsyncMock(
        return_value={
            "status": "success",
            "status_code": 200,
            "data": {"ok": True},
        }
    )
    monkeypatch.setattr(tool_route, "execute_http_tool", executor)

    resp = await call_test_tool_route(
        "tu-http",
        request=ToolTestRequest(
            llm_params={"query": "cart"},
            preset_params={
                "customer_id": "c_123",
                "sentiment": "cooperative",
            },
        ),
        user=_fake_user(),
    )

    assert resp.status == "success"
    assert resp.status_code == 200
    assert resp.data == {"ok": True}
    assert resp.error is None
    executor.assert_awaited_once_with(
        tool,
        {"query": "cart"},
        preset_params={
            "customer_id": "c_123",
            "sentiment": "cooperative",
        },
        organization_id=1,
        include_request_headers=True,
    )
    assert resp.hint is None
    assert resp.request_method == "GET"
    assert resp.request_url == "https://example.com/search"
    assert resp.request_headers == {}
    assert resp.request_body is None
    assert resp.request_params == {
        "query": "cart",
        "customer_id": "c_123",
        "sentiment": "cooperative",
    }


@pytest.mark.asyncio
async def test_tool_test_sets_request_body_for_post_method(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="POST")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(
            return_value={"status": "success", "status_code": 200, "data": {"id": 1}}
        ),
    )

    resp = await call_test_tool_route(
        "tu-http",
        request=ToolTestRequest(llm_params={"name": "Ada"}),
        user=_fake_user(),
    )

    assert resp.request_method == "POST"
    assert resp.request_body == {"name": "Ada"}
    assert resp.request_params is None


@pytest.mark.asyncio
async def test_tool_test_returns_masked_effective_request_headers(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="POST")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(
            return_value={
                "status": "success",
                "status_code": 200,
                "data": {"ok": True},
                "request_headers": {
                    "X-Tenant": "acme",
                    "Authorization": "****************oken",
                },
            }
        ),
    )

    resp = await call_test_tool_route(
        "tu-http", request=ToolTestRequest(), user=_fake_user()
    )

    assert resp.request_headers == {
        "X-Tenant": "acme",
        "Authorization": "****************oken",
    }


@pytest.mark.asyncio
async def test_tool_test_request_body_includes_resolved_preset_parameters(
    monkeypatch,
):
    """The Request preview includes direct preset values alongside LLM values."""
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="POST")
    tool.definition["config"]["preset_parameters"] = [
        {
            "name": "source",
            "type": "string",
            "value_template": "{{initial_context.metadata.channel}}",
            "required": True,
        }
    ]
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(
            return_value={"status": "success", "status_code": 200, "data": {"ok": True}}
        ),
    )

    resp = await call_test_tool_route(
        "tu-http",
        request=ToolTestRequest(
            llm_params={"name": "Ada"},
            preset_params={"source": "web_widget"},
        ),
        user=_fake_user(),
    )

    assert resp.request_body == {"name": "Ada", "source": "web_widget"}


@pytest.mark.asyncio
async def test_tool_test_no_arguments_post_shows_empty_body(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="POST")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(return_value={"status": "success", "status_code": 200, "data": None}),
    )

    resp = await call_test_tool_route(
        "tu-http", request=ToolTestRequest(), user=_fake_user()
    )

    # POST with no arguments sends json={} over the wire; preview must show {}
    # so callers can distinguish an absent body from an empty one.
    assert resp.request_body == {}
    assert resp.request_params is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_snippet",
    [
        (400, "HTTP 400 Bad Request"),
        (401, "HTTP 401 Unauthorized"),
        (403, "HTTP 403 Forbidden"),
        (404, "HTTP 404 Not Found"),
        (405, "HTTP 405 Method Not Allowed"),
        (408, "HTTP 408 Request Timeout"),
        (409, "HTTP 409 Conflict"),
        (415, "HTTP 415 Unsupported Media Type"),
        (422, "HTTP 422 Unprocessable Entity"),
        (429, "HTTP 429 Too Many Requests"),
        (500, "HTTP 500"),
        (503, "HTTP 503"),
    ],
)
async def test_tool_test_hint_for_status_code(
    monkeypatch, status_code, expected_snippet
):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="POST")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(
            return_value={
                "status": "error",
                "status_code": status_code,
                "error": "boom",
            }
        ),
    )

    resp = await call_test_tool_route(
        "tu-http", request=ToolTestRequest(llm_params={"a": 1}), user=_fake_user()
    )

    assert resp.hint is not None
    assert resp.hint.startswith(expected_snippet)


@pytest.mark.asyncio
async def test_tool_test_no_hint_on_success(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="GET")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(return_value={"status": "success", "status_code": 200, "data": {}}),
    )

    resp = await call_test_tool_route(
        "tu-http", request=ToolTestRequest(), user=_fake_user()
    )

    assert resp.hint is None


@pytest.mark.asyncio
async def test_tool_test_no_hint_for_uncovered_status_code(monkeypatch):
    import api.routes.tool as tool_route

    tool = _http_tool_model(method="GET")
    monkeypatch.setattr(
        tool_route.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_route,
        "execute_http_tool",
        AsyncMock(
            return_value={"status": "error", "status_code": 418, "error": "teapot"}
        ),
    )

    resp = await call_test_tool_route(
        "tu-http", request=ToolTestRequest(), user=_fake_user()
    )

    assert resp.hint is None


def test_tool_test_route_is_registered():
    assert any(
        route.path == "/tools/{tool_uuid}/test" and "POST" in route.methods
        for route in router.routes
    )


@pytest.mark.asyncio
async def test_tool_rejects_non_http_api_tool(monkeypatch):
    import api.routes.tool as tool_route

    monkeypatch.setattr(
        tool_route.db_client,
        "get_tool_by_uuid",
        AsyncMock(return_value=_mcp_tool_model()),
    )

    with pytest.raises(HTTPException) as ei:
        await call_test_tool_route(
            "tu-mcp", request=ToolTestRequest(), user=_fake_user()
        )

    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_refresh_success(monkeypatch):
    import api.services.tool_management as tool_svc

    tool = _mcp_tool_model()
    monkeypatch.setattr(
        tool_svc.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(
        tool_svc.db_client,
        "update_tool",
        AsyncMock(return_value=tool),
    )
    monkeypatch.setattr(
        tool_svc,
        "discover_mcp_tools",
        AsyncMock(return_value=[{"name": "echo", "description": "Echo"}]),
    )
    resp = await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert resp.discovered_tools == [{"name": "echo", "description": "Echo"}]
    assert resp.error is None


@pytest.mark.asyncio
async def test_refresh_server_down_returns_200_with_error(monkeypatch):
    import api.services.tool_management as tool_svc

    tool = _mcp_tool_model()
    monkeypatch.setattr(
        tool_svc.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    monkeypatch.setattr(tool_svc.db_client, "update_tool", AsyncMock(return_value=tool))
    monkeypatch.setattr(tool_svc, "discover_mcp_tools", AsyncMock(return_value=[]))
    resp = await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert resp.discovered_tools == []
    assert resp.error  # non-empty human-readable message
    # update_tool should NOT be called when discovery returns empty
    tool_svc.db_client.update_tool.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_non_mcp_is_400(monkeypatch):
    import api.services.tool_management as tool_svc

    tool = _mcp_tool_model()
    tool.category = "http_api"
    monkeypatch.setattr(
        tool_svc.db_client, "get_tool_by_uuid", AsyncMock(return_value=tool)
    )
    with pytest.raises(HTTPException) as ei:
        await refresh_mcp_tools("tu-mcp", user=_fake_user())
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_refresh_not_found_is_404(monkeypatch):
    import api.services.tool_management as tool_svc

    monkeypatch.setattr(
        tool_svc.db_client, "get_tool_by_uuid", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as ei:
        await refresh_mcp_tools("nope", user=_fake_user())
    assert ei.value.status_code == 404


def test_tool_test_response_has_hint_and_request_fields():
    """ToolTestResponse must carry hint + request_method/url/body/params
    so the frontend can show what was sent and why it may have failed."""
    resp = ToolTestResponse(
        status="error",
        status_code=405,
        data=None,
        error="Method Not Allowed",
        duration_ms=12,
        hint="HTTP 405 Method Not Allowed — the endpoint rejected the configured method (POST).",
        request_method="POST",
        request_url="https://example.com/thing",
        request_headers={"Authorization": "********oken"},
        request_body={"a": 1},
        request_params=None,
    )
    assert resp.hint.startswith("HTTP 405")
    assert resp.request_method == "POST"
    assert resp.request_url == "https://example.com/thing"
    assert resp.request_headers == {"Authorization": "********oken"}
    assert resp.request_body == {"a": 1}
    assert resp.request_params is None


def test_tool_test_response_request_fields_default_to_none_or_required():
    """hint/request_body/request_params are optional; request_method/url are required."""
    resp = ToolTestResponse(
        status="success",
        duration_ms=5,
        request_method="GET",
        request_url="https://example.com/thing",
    )
    assert resp.hint is None
    assert resp.request_headers == {}
    assert resp.request_body is None
    assert resp.request_params is None
