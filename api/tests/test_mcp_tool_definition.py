import importlib

import pytest

from api.enums import ToolCategory
from api.routes.tool import McpToolConfig as RouteMcpToolConfig
from api.routes.tool import McpToolDefinition as RouteMcpToolDefinition
from api.services.workflow.tools.mcp_tool import (
    McpDefinitionError,
    McpToolConfig,
    McpToolDefinition,
    namespace_function_name,
    validate_mcp_definition,
)


def test_mcp_category_exists():
    assert ToolCategory.MCP.value == "mcp"
    assert ToolCategory("mcp") is ToolCategory.MCP


def test_mcp_migration_present_and_chained(monkeypatch):
    mod = importlib.import_module(
        "api.alembic.versions.0a1b2c3d4e5f_add_mcp_in_toolcategory"
    )
    assert mod.revision == "0a1b2c3d4e5f"
    assert mod.down_revision == "4c1f1e3e8ef2"

    calls = []

    def fake_sync_enum_values(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(mod.op, "sync_enum_values", fake_sync_enum_values)

    mod.upgrade()
    mod.downgrade()

    assert len(calls) == 2
    assert calls[0]["enum_name"] == "tool_category"
    assert "mcp" in calls[0]["new_values"]
    assert "mcp" not in calls[1]["new_values"]


def test_route_reuses_shared_mcp_models():
    assert RouteMcpToolConfig is McpToolConfig
    assert RouteMcpToolDefinition is McpToolDefinition


def test_validate_mcp_definition_ok():
    cfg = validate_mcp_definition(
        {
            "schema_version": 1,
            "type": "mcp",
            "config": {
                "transport": "streamable_http",
                "url": "https://acme.example.com/mcp",
                "credential_uuid": "cred-123",
                "tools_filter": ["lookup_patient"],
                "timeout_secs": 30,
                "sse_read_timeout_secs": 300,
            },
        }
    )
    assert cfg["url"] == "https://acme.example.com/mcp"
    assert cfg["transport"] == "streamable_http"
    assert cfg["tools_filter"] == ["lookup_patient"]
    assert cfg["timeout_secs"] == 30
    assert cfg["sse_read_timeout_secs"] == 300
    assert cfg["credential_uuid"] == "cred-123"


def test_validate_mcp_definition_defaults():
    cfg = validate_mcp_definition({"type": "mcp", "config": {"url": "https://x/mcp"}})
    assert cfg["transport"] == "streamable_http"
    assert cfg["tools_filter"] == []
    assert cfg["timeout_secs"] == 30
    assert cfg["sse_read_timeout_secs"] == 300
    assert cfg["credential_uuid"] is None


@pytest.mark.parametrize(
    "definition",
    [
        {"type": "mcp", "config": {}},
        {"type": "mcp", "config": {"url": ""}},
        {"type": "mcp", "config": {"url": "ftp://x"}},
        {"type": "mcp"},
        {"type": "mcp", "config": {"url": "https://x", "transport": "stdio"}},
    ],
)
def test_validate_mcp_definition_rejects(definition):
    with pytest.raises(McpDefinitionError):
        validate_mcp_definition(definition)


def test_validate_mcp_definition_zero_timeout_preserved():
    cfg = validate_mcp_definition(
        {"type": "mcp", "config": {"url": "https://x/mcp", "timeout_secs": 0}}
    )
    assert cfg["timeout_secs"] == 0


def test_namespace_function_name():
    assert (
        namespace_function_name("Acme MCP", "lookup_patient")
        == "mcp__acme_mcp__lookup_patient"
    )
    assert (
        namespace_function_name("", "ping", fallback="abcd1234")
        == "mcp__abcd1234__ping"
    )
