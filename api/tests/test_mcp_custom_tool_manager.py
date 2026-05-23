import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.enums import ToolCategory
from api.services.workflow.mcp_tool_session import McpToolSession
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.tests.support.mcp_mock_server import running_mcp_server


def _mcp_tool():
    t = MagicMock()
    t.tool_uuid = "uuid-" + uuid.uuid4().hex[:8]
    t.name = "Acme MCP"
    t.category = ToolCategory.MCP.value
    t.definition = {"type": "mcp", "config": {"url": "https://x/mcp"}}
    return t


@pytest.mark.asyncio
async def test_get_tool_schemas_and_handler_for_mcp(monkeypatch):
    async with running_mcp_server() as base_url:
        tool = _mcp_tool()
        session = McpToolSession(
            tool_uuid=tool.tool_uuid,
            tool_name=tool.name,
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
        await session.start()

        engine = MagicMock()
        engine._mcp_sessions = {tool.tool_uuid: session}
        registered = {}
        reg_kwargs = {}

        def _reg(name, fn, **kw):
            registered[name] = fn
            reg_kwargs[name] = kw

        engine.llm.register_function = _reg

        mgr = CustomToolManager(engine)
        mgr.get_organization_id = AsyncMock(return_value=42)

        from api.db import db_client

        monkeypatch.setattr(
            db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool])
        )

        try:
            schemas = await mgr.get_tool_schemas([tool.tool_uuid])
            names = sorted(s.name for s in schemas)
            assert names == ["mcp__acme_mcp__add", "mcp__acme_mcp__echo"]

            await mgr.register_handlers([tool.tool_uuid])
            assert "mcp__acme_mcp__echo" in registered
            assert reg_kwargs["mcp__acme_mcp__echo"]["timeout_secs"] == pytest.approx(
                15.0
            )

            captured = {}

            class P:
                function_name = "mcp__acme_mcp__echo"
                arguments = {"text": "yo"}

                async def result_callback(self, r, *, properties=None):
                    captured["r"] = r

            await registered["mcp__acme_mcp__echo"](P())
            assert "echo:yo" in str(captured["r"])
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_unavailable_mcp_session_contributes_nothing(monkeypatch):
    tool = _mcp_tool()
    session = McpToolSession(
        tool_uuid=tool.tool_uuid,
        tool_name=tool.name,
        url="http://127.0.0.1:1/mcp",
        credential=None,
        tools_filter=[],
        timeout_secs=1,
        sse_read_timeout_secs=1,
    )
    await session.start()  # degrades

    engine = MagicMock()
    engine._mcp_sessions = {tool.tool_uuid: session}
    mgr = CustomToolManager(engine)
    mgr.get_organization_id = AsyncMock(return_value=42)

    from api.db import db_client

    monkeypatch.setattr(db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool]))

    schemas = await mgr.get_tool_schemas([tool.tool_uuid])
    assert schemas == []
    await mgr.register_handlers([tool.tool_uuid])  # must not raise


def test_call_timeout_secs_is_read_timeout_plus_buffer():
    session = McpToolSession(
        tool_uuid="uuid-abc123",
        tool_name="Acme MCP",
        url="https://x/mcp",
        credential=None,
        tools_filter=[],
        timeout_secs=10,
        sse_read_timeout_secs=20,
    )
    assert session.call_timeout_secs == 25.0


@pytest.mark.asyncio
async def test_per_node_mcp_filter_intersection(monkeypatch):
    async with running_mcp_server() as base_url:
        tool = _mcp_tool()
        session = McpToolSession(
            tool_uuid=tool.tool_uuid,
            tool_name=tool.name,
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
        await session.start()

        engine = MagicMock()
        engine._mcp_sessions = {tool.tool_uuid: session}
        registered = {}
        engine.llm.register_function = lambda name, fn, **kw: registered.__setitem__(
            name, fn
        )

        mgr = CustomToolManager(engine)
        mgr.get_organization_id = AsyncMock(return_value=42)

        from api.db import db_client

        monkeypatch.setattr(
            db_client, "get_tools_by_uuids", AsyncMock(return_value=[tool])
        )
        try:
            # Allow only raw "echo" for this node
            filters = {tool.tool_uuid: ["echo"]}
            schemas = await mgr.get_tool_schemas(
                [tool.tool_uuid], mcp_tool_filters=filters
            )
            # Check only "echo" schema returned (namespaced name depends on tool.name)
            assert len(schemas) == 1
            assert all("echo" in s.name for s in schemas)

            await mgr.register_handlers([tool.tool_uuid], mcp_tool_filters=filters)
            assert len(registered) == 1
            assert all("echo" in k for k in registered)

            # No filter entry for this uuid = none (default-none)
            registered.clear()
            result = await mgr.get_tool_schemas([tool.tool_uuid], mcp_tool_filters={})
            assert result == []
            await mgr.register_handlers([tool.tool_uuid], mcp_tool_filters={})
            assert registered == {}

            # mcp_tool_filters=None = backward-compatible (all tools)
            registered.clear()
            all_schemas = await mgr.get_tool_schemas([tool.tool_uuid])
            assert len(all_schemas) == 2  # both echo and add
            await mgr.register_handlers([tool.tool_uuid])
            assert len(registered) == 2  # both handlers registered
        finally:
            await session.close()
