from datetime import timedelta
from unittest.mock import MagicMock

import httpx
import pytest

from api.services.workflow.mcp_tool_session import (
    McpToolSession,
    build_streamable_http_params,
    discover_mcp_tools,
)
from api.tests.support.mcp_mock_server import running_mcp_server


@pytest.mark.asyncio
async def test_mock_server_starts_and_serves():
    async with running_mcp_server() as base_url:
        async with httpx.AsyncClient() as client:
            resp = await client.get(base_url, timeout=5.0)
        assert resp.status_code in (400, 404, 405, 406)


def test_build_streamable_http_params_with_credential():
    cred = MagicMock()
    cred.credential_type = "bearer_token"
    cred.credential_data = {"token": "abc"}
    params = build_streamable_http_params(
        url="https://acme.example.com/mcp",
        credential=cred,
        timeout_secs=30,
        sse_read_timeout_secs=300,
    )
    assert params.url == "https://acme.example.com/mcp"
    assert params.headers == {"Authorization": "Bearer abc"}
    assert params.timeout == timedelta(seconds=30)
    assert params.sse_read_timeout == timedelta(seconds=300)


def test_build_streamable_http_params_no_credential():
    params = build_streamable_http_params(
        url="https://acme.example.com/mcp",
        credential=None,
        timeout_secs=10,
        sse_read_timeout_secs=20,
    )
    assert params.headers is None or params.headers == {}


@pytest.mark.asyncio
async def test_session_start_passes_auth_header_to_real_server():
    cred = MagicMock()
    cred.credential_type = "bearer_token"
    cred.credential_data = {"token": "abc"}

    async with running_mcp_server(
        required_headers={"Authorization": "Bearer abc"}
    ) as base_url:
        session = McpToolSession(
            tool_uuid="uuid-auth-ok",
            tool_name="Secure MCP",
            url=base_url,
            credential=cred,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=20,
        )
        await session.start()
        try:
            assert session.available is True
            names = sorted(s.name for s in session.function_schemas())
            assert names == ["mcp__secure_mcp__add", "mcp__secure_mcp__echo"]
            result = await session.call("mcp__secure_mcp__echo", {"text": "hi"})
            assert "echo:hi" in result
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_session_auth_failure_degrades_not_raises():
    async with running_mcp_server(
        required_headers={"Authorization": "Bearer abc"}
    ) as base_url:
        session = McpToolSession(
            tool_uuid="uuid-auth-fail",
            tool_name="Secure MCP",
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=2,
            sse_read_timeout_secs=2,
        )
        await session.start()  # must degrade instead of raising on 401
        try:
            assert session.available is False
            assert session.function_schemas() == []
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_session_start_lists_and_calls_real_server():
    async with running_mcp_server() as base_url:
        session = McpToolSession(
            tool_uuid="uuid-1234abcd",
            tool_name="Acme MCP",
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=20,
        )
        await session.start()
        try:
            assert session.available is True
            schemas = session.function_schemas()
            names = sorted(s.name for s in schemas)
            assert names == ["mcp__acme_mcp__add", "mcp__acme_mcp__echo"]
            result = await session.call("mcp__acme_mcp__echo", {"text": "hi"})
            assert "echo:hi" in result
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_session_tools_filter_applied():
    async with running_mcp_server() as base_url:
        session = McpToolSession(
            tool_uuid="uuid-1234abcd",
            tool_name="Acme MCP",
            url=base_url,
            credential=None,
            tools_filter=["echo"],
            timeout_secs=10,
            sse_read_timeout_secs=20,
        )
        await session.start()
        try:
            names = sorted(s.name for s in session.function_schemas())
            assert names == ["mcp__acme_mcp__echo"]
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_session_unreachable_degrades_not_raises():
    session = McpToolSession(
        tool_uuid="uuid-1234abcd",
        tool_name="Acme MCP",
        url="http://127.0.0.1:1/mcp",
        credential=None,
        tools_filter=[],
        timeout_secs=2,
        sse_read_timeout_secs=2,
    )
    await session.start()  # must NOT raise
    assert session.available is False
    assert session.function_schemas() == []
    await session.close()


@pytest.mark.asyncio
async def test_call_on_unavailable_session_raises():
    session = McpToolSession(
        tool_uuid="uuid-1234abcd",
        tool_name="Acme MCP",
        url="http://127.0.0.1:1/mcp",
        credential=None,
        tools_filter=[],
        timeout_secs=2,
        sse_read_timeout_secs=2,
    )
    await session.start()
    with pytest.raises(RuntimeError):
        await session.call("mcp__acme_mcp__echo", {"text": "x"})
    await session.close()


@pytest.mark.asyncio
async def test_call_unknown_function_raises():
    async with running_mcp_server() as base_url:
        session = McpToolSession(
            tool_uuid="uuid-1234abcd",
            tool_name="Acme MCP",
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
        await session.start()
        try:
            with pytest.raises(RuntimeError):
                await session.call("mcp__acme_mcp__does_not_exist", {})
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_function_schemas_filter_by_raw_name():
    async with running_mcp_server() as base_url:
        session = McpToolSession(
            tool_uuid="t-filter",
            tool_name="Mock MCP",
            url=base_url,
            credential=None,
            tools_filter=[],
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
        await session.start()
        try:
            # No arg = all (backward compatible)
            all_names = sorted(s.name for s in session.function_schemas())
            assert all_names == ["mcp__mock_mcp__add", "mcp__mock_mcp__echo"]

            # Allow only raw "echo"
            only_echo = session.function_schemas(allowed_raw_names={"echo"})
            assert [s.name for s in only_echo] == ["mcp__mock_mcp__echo"]

            # Empty set = none (default-none semantics)
            assert session.function_schemas(allowed_raw_names=set()) == []

            # Unknown raw name = skipped (pure intersection)
            assert session.function_schemas(allowed_raw_names={"nope"}) == []
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_discover_mcp_tools_success():
    async with running_mcp_server() as base_url:
        tools = await discover_mcp_tools(
            url=base_url,
            credential=None,
            timeout_secs=10,
            sse_read_timeout_secs=10,
        )
    names = sorted(t["name"] for t in tools)
    assert names == ["add", "echo"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["echo"]["description"]  # non-empty description
    assert set(by_name["echo"]) == {"name", "description"}


@pytest.mark.asyncio
async def test_discover_mcp_tools_server_down_returns_empty():
    # Unroutable port, short timeouts: must degrade to [] (never raise).
    tools = await discover_mcp_tools(
        url="http://127.0.0.1:1/mcp",
        credential=None,
        timeout_secs=1,
        sse_read_timeout_secs=1,
    )
    assert tools == []


def test_agent_node_data_carries_mcp_tool_filters():
    from api.services.workflow.dto import AgentNodeData, NodeType
    from api.services.workflow.workflow_graph import Node

    data = AgentNodeData(
        name="N1",
        tool_uuids=["tu-1"],
        mcp_tool_filters={"tu-1": ["echo"]},
    )
    assert data.mcp_tool_filters == {"tu-1": ["echo"]}

    node = Node("n1", NodeType.agentNode, data)
    assert node.mcp_tool_filters == {"tu-1": ["echo"]}

    # Absent field defaults to None (backward compatible)
    data2 = AgentNodeData(name="N2")
    assert data2.mcp_tool_filters is None
    assert Node("n2", NodeType.agentNode, data2).mcp_tool_filters is None
