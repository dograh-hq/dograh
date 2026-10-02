"""Tests for LLM tools that integration packages add to conversational nodes.

Covers the generic path, independent of any concrete integration:
- the registry collects tools from every package and isolates a failing factory
- compose_functions_for_node adds integration tool schemas
- PipecatEngine registers integration tools on Start Call and Agent nodes,
  using the graph of the agent that owns the node, and skips End Call nodes
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, Mock, patch

from api.services.integrations import registry
from api.services.integrations.base import IntegrationPackageSpec, IntegrationTool
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)


def _tool(name="lookup_order", handler=None) -> IntegrationTool:
    return IntegrationTool(
        name=name,
        description="Look up an order by number.",
        properties={"order_number": {"type": "string", "description": "Order"}},
        required=("order_number",),
        handler=handler or AsyncMock(),
    )


def test_create_integration_tools_skips_a_failing_factory(monkeypatch):
    good = _tool()

    def broken(_graph):
        raise RuntimeError("boom")

    registry.all_packages()
    monkeypatch.setattr(
        registry,
        "_PACKAGE_REGISTRY",
        {
            "a_broken": IntegrationPackageSpec(name="a_broken", create_tools=broken),
            "b_good": IntegrationPackageSpec(
                name="b_good", create_tools=lambda _graph: [good]
            ),
            "c_no_tools": IntegrationPackageSpec(name="c_no_tools"),
        },
    )

    assert registry.create_integration_tools(object()) == [good]


async def test_compose_functions_includes_integration_tools():
    node = types.SimpleNamespace(document_uuids=None, tool_uuids=None, out_edges=[])

    functions = await compose_functions_for_node(
        node=node, custom_tool_manager=None, integration_tools=[_tool()]
    )

    assert [schema.name for schema in functions] == ["lookup_order"]
    assert functions[0].required == ["order_number"]
    assert "order_number" in functions[0].properties


async def test_engine_registers_integration_tools_on_conversational_nodes(
    three_node_workflow,
):
    handler = AsyncMock()
    llm = Mock()
    llm._update_settings = AsyncMock()
    engine = PipecatEngine(workflow=three_node_workflow, llm=llm, call_context_vars={})
    agent = engine.active_agent

    with patch(
        "api.services.workflow.pipecat_engine.create_integration_tools",
        return_value=[_tool(handler=handler)],
    ) as create_tools:
        for node_id in ("start", "agent"):
            llm.register_function.reset_mock()
            await engine._prepare_node(agent, three_node_workflow.nodes[node_id])

            assert "lookup_order" in {t.name for t in agent.tools.standard_tools}
            name, bound = llm.register_function.call_args.args
            assert name == "lookup_order"

        create_tools.assert_called_with(agent.workflow)

        params = types.SimpleNamespace(arguments={"order_number": "42"})
        await bound(params)
        handler.assert_awaited_once_with(params)

        create_tools.reset_mock()
        await engine._prepare_node(agent, three_node_workflow.nodes["end"])

    create_tools.assert_not_called()
    assert "lookup_order" not in {t.name for t in agent.tools.standard_tools}
