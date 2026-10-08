"""Contracts shared by external MCP harnesses and the in-app builder."""

import re
from types import SimpleNamespace

import pytest
from fastmcp import Client

from api.mcp_server.auth import builder_mcp_user
from api.mcp_server.server import mcp
from api.mcp_server.ts_bridge import parse_code
from api.services.voice_prompting_guide import Stage, build_briefing, get_topic
from api.services.workflow.authoring import (
    authoring_stage,
    render_authoring_instructions,
)
from api.services.workflow.authoring.contracts import AuthoringStage
from api.services.workflow.builder import call_builder_tool
from api.services.workflow.builder_runtime.agent import builder_instructions
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


@pytest.mark.asyncio
async def test_mcp_loads_the_same_stage_guidance_as_each_builder_agent():
    core = render_authoring_instructions("").strip()
    async with Client(mcp) as client:
        instructions = client.instructions
    assert instructions is not None
    assert instructions.startswith(core)
    assert "@dograh/sdk" not in core
    assert "get_workflow_authoring_guide" in core
    # Interface-specific capabilities must not leak across bootstrap boundaries.
    assert "`propose_workflow`" not in instructions
    assert "`create_workflow`" not in builder_instructions()
    assert "`save_workflow`" not in builder_instructions()
    token = builder_mcp_user.set(SimpleNamespace(id=3, selected_organization_id=27))
    try:
        async with Client(mcp) as client:
            for stage in AuthoringStage:
                result = (
                    await client.call_tool(
                        "get_workflow_authoring_guide", {"stage": stage.value}
                    )
                ).data
                assert result == authoring_stage(stage)
                assert builder_instructions(stage).startswith(result["instructions"])
                assert result["output_schema"]["required"]
                if stage != AuthoringStage.build:
                    assert "@dograh/sdk" not in result["instructions"]
    finally:
        builder_mcp_user.reset(token)


@pytest.mark.asyncio
async def test_shared_syntax_example_passes_the_real_parser_and_graph_validation():
    examples = re.findall(
        r"```typescript\n(.*?)```", authoring_stage("build")["instructions"], re.DOTALL
    )
    assert examples
    for example in examples:
        parsed = await parse_code(example)
        assert parsed["ok"], parsed
        WorkflowGraph(ReactFlowDTO.model_validate(parsed["workflow"]))


@pytest.mark.parametrize("stage", [Stage.create, Stage.review])
def test_required_global_guidance_is_scoped_by_the_topic(stage):
    global_topics = build_briefing(stage, "globalNode")["topics"]
    assert any(
        topic["id"] == "common_guidelines" and topic["required_read"]
        for topic in global_topics
    )
    assert not any(
        topic["id"] == "common_guidelines"
        for topic in build_briefing(stage, "agentNode")["topics"]
    )
    assert not any(
        topic["required_read"] for topic in build_briefing(Stage.plan)["topics"]
    )


@pytest.mark.asyncio
async def test_topic_and_lens_changes_reach_mcp_and_builder_without_copying(
    monkeypatch,
):
    topic = get_topic("common_guidelines")
    monkeypatch.setattr(topic, "content", "Updated canonical global template.")
    monkeypatch.setattr(topic.stages[Stage.create], "lens", "Updated create lens.")
    token = builder_mcp_user.set(SimpleNamespace(id=3, selected_organization_id=27))
    try:
        async with Client(mcp) as client:
            for arguments in (
                {"stage": "create", "node_type": "globalNode"},
                {"topic": "common_guidelines"},
            ):
                external = (
                    await client.call_tool("get_voice_prompting_guide", arguments)
                ).data
                builder = await call_builder_tool(
                    client, "get_voice_prompting_guide", arguments
                )
                assert external == builder
                if "topic" in arguments:
                    assert builder["content"] == "Updated canonical global template."
                else:
                    lens = next(t for t in builder["topics"] if t["id"] == topic.id)
                    assert lens["lens"] == "Updated create lens."
                    assert lens["required_read"] is True
    finally:
        builder_mcp_user.reset(token)
