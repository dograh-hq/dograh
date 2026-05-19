import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.enums import ToolCategory
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.support.mcp_mock_server import running_mcp_server


def _mcp_tool(url: str):
    t = MagicMock()
    t.tool_uuid = "uuid-" + uuid.uuid4().hex[:8]
    t.name = "Acme MCP"
    t.category = ToolCategory.MCP.value
    t.definition = {
        "schema_version": 1,
        "type": "mcp",
        "config": {"transport": "streamable_http", "url": url},
    }
    return t


@pytest.mark.asyncio
async def test_engine_opens_and_closes_mcp_sessions(monkeypatch):
    async with running_mcp_server() as base_url:
        tool = _mcp_tool(base_url)

        engine = PipecatEngine.__new__(PipecatEngine)
        node = MagicMock()
        node.tool_uuids = [tool.tool_uuid]
        workflow = MagicMock()
        workflow.nodes = {"n1": node}
        engine.workflow = workflow
        engine._mcp_sessions = {}

        from api.db import db_client

        monkeypatch.setattr(
            db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool])
        )
        monkeypatch.setattr(
            db_client, "get_credential_by_uuid", AsyncMock(return_value=None)
        )
        engine._get_organization_id = AsyncMock(return_value=42)

        await engine._open_mcp_sessions()
        try:
            assert tool.tool_uuid in engine._mcp_sessions
            sess = engine._mcp_sessions[tool.tool_uuid]
            assert sess.available is True
            assert len(sess.function_schemas()) == 2
        finally:
            await engine._close_mcp_sessions()
        assert engine._mcp_sessions == {}


@pytest.mark.asyncio
async def test_open_mcp_sessions_swallows_db_error(monkeypatch):
    engine = PipecatEngine.__new__(PipecatEngine)
    node = MagicMock()
    node.tool_uuids = ["uuid-deadbeef"]
    workflow = MagicMock()
    workflow.nodes = {"n1": node}
    engine.workflow = workflow
    engine._mcp_sessions = {}

    from api.db import db_client

    monkeypatch.setattr(
        db_client,
        "get_tools_by_uuids",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    engine._get_organization_id = AsyncMock(return_value=42)

    # Must NOT raise
    await engine._open_mcp_sessions()
    assert engine._mcp_sessions == {}


@pytest.mark.asyncio
async def test_open_mcp_sessions_skips_tool_when_credential_fetch_fails(monkeypatch):
    tool = _mcp_tool("http://127.0.0.1:1/mcp")
    tool.definition["config"]["credential_uuid"] = "cred-1234"

    engine = PipecatEngine.__new__(PipecatEngine)
    node = MagicMock()
    node.tool_uuids = [tool.tool_uuid]
    workflow = MagicMock()
    workflow.nodes = {"n1": node}
    engine.workflow = workflow
    engine._mcp_sessions = {}

    from api.db import db_client

    monkeypatch.setattr(db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool]))
    monkeypatch.setattr(
        db_client,
        "get_credential_by_uuid",
        AsyncMock(side_effect=RuntimeError("cred store down")),
    )
    engine._get_organization_id = AsyncMock(return_value=42)

    # Must NOT raise, and must skip the tool (no futile unauthenticated start)
    await engine._open_mcp_sessions()
    assert engine._mcp_sessions == {}
