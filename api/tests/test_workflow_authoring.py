"""Contracts shared by external MCP harnesses and the in-app builder."""

import re
from types import SimpleNamespace

import pytest
from fastmcp import Client

from api.mcp_server.auth import builder_mcp_user
from api.mcp_server.server import mcp
from api.mcp_server.ts_bridge import parse_code
from api.services.voice_prompting_guide import (
    get_topic,
    list_topic_index,
    render_guide,
)
from api.services.workflow.authoring import (
    authoring_stage,
    render_authoring_instructions,
)
from api.services.workflow.authoring.contracts import AuthoringStage
from api.services.workflow.authoring.sdk_reference import CORE_NODE_TYPES
from api.services.workflow.builder import call_builder_tool
from api.services.workflow.builder_runtime.agent import (
    STAGE_MCP_TOOLS,
    builder_instructions,
)
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


@pytest.mark.asyncio
async def test_mcp_loads_the_same_stage_guidance_as_each_builder_agent():
    assert {stage.value for stage in AuthoringStage} == {
        "build_requirements",
        "build",
        "review",
    }
    assert set(STAGE_MCP_TOOLS) == {"build_requirements", "build"}
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
            removed = await client.call_tool(
                "get_workflow_authoring_guide", {"stage": "plan"}, raise_on_error=False
            )
            assert removed.is_error
            for stage in AuthoringStage:
                result = (
                    await client.call_tool(
                        "get_workflow_authoring_guide", {"stage": stage.value}
                    )
                ).data
                assert result == authoring_stage(stage)
                if stage.value in STAGE_MCP_TOOLS:
                    assert builder_instructions(stage).startswith(
                        result["instructions"]
                    )
                assert result["output_schema"]["required"]
                if stage == AuthoringStage.build_requirements:
                    assert "open_questions" not in result["output_schema"]["properties"]
                if stage != AuthoringStage.build:
                    assert "@dograh/sdk" not in result["instructions"]
    finally:
        builder_mcp_user.reset(token)


@pytest.mark.parametrize("stage", list(AuthoringStage))
def test_creation_guidance_has_no_catalog_discovery_or_integration_node_schemas(stage):
    instructions = authoring_stage(stage)["instructions"]
    for name in (
        "list_node_types",
        "get_node_type",
        "list_documents",
        "list_credentials",
        "list_recordings",
        "list_tools",
        "noveum",
        "tuner",
    ):
        assert name not in instructions
    if stage in (AuthoringStage.build, AuthoringStage.review):
        for name in CORE_NODE_TYPES:
            assert f"{name}({{" in instructions
        assert "extraction_enabled?: boolean" in instructions
        assert 'type: "string" | "number" | "boolean"' in instructions
    if stage == AuthoringStage.build:
        assert "get_voice_prompting_guide" in instructions


def test_only_iteration_build_and_review_expose_resource_guidance():
    for stage in AuthoringStage:
        instructions = authoring_stage(stage, iteration=True)["instructions"]
        assert ("`list_credentials`" in instructions) == (
            stage in (AuthoringStage.build, AuthoringStage.review)
        )


def test_editor_prompt_is_separate_from_first_draft_requirements_and_building():
    creation = builder_instructions("build")
    editing = builder_instructions("build", iteration=True)
    assert editing.startswith(authoring_stage("build", iteration=True)["instructions"])
    assert "# Editor:" in editing
    assert "# Builder:" not in editing
    assert "approved CallBrief" not in editing
    assert "`submit_brief`" not in editing
    assert "`request_clarification`" not in editing
    assert "`ask_questions`" in editing
    assert "`propose_tool`" in editing and "`propose_tool_edit`" in editing
    assert "tool_uuids" in editing
    assert "# Editor:" not in creation
    assert "`ask_questions`" not in creation
    assert "`propose_tool`" not in creation
    assert "approved CallBrief" in creation


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


@pytest.mark.asyncio
async def test_voice_guide_returns_content_directly_and_advertises_topics():
    token = builder_mcp_user.set(SimpleNamespace(id=3, selected_organization_id=27))
    try:
        async with Client(mcp) as client:
            spec = next(
                tool
                for tool in await client.list_tools()
                if tool.name == "get_voice_prompting_guide"
            )
            assert set(spec.input_schema["properties"]) == {"topic"}
            assert not spec.input_schema.get("required")
            full = (await client.call_tool("get_voice_prompting_guide", {})).data
            assert full == {"content": render_guide()}
            for entry in list_topic_index():
                assert (
                    entry["id"]
                    in spec.input_schema["properties"]["topic"]["description"]
                )
                topic = get_topic(entry["id"])
                assert full["content"].count(topic.content.strip()) == 1

            focused = (
                await client.call_tool(
                    "get_voice_prompting_guide", {"topic": "tool_calls"}
                )
            ).data
            assert focused == {"content": render_guide("tool_calls")}
            assert get_topic("tool_calls").content.strip() in focused["content"]
            assert (
                get_topic("common_guidelines").content.strip() not in focused["content"]
            )

            invalid = await client.call_tool(
                "get_voice_prompting_guide", {"topic": "unknown"}, raise_on_error=False
            )
            assert invalid.is_error
            error = " ".join(block.text for block in invalid.content)
            assert "Unknown voice-prompting topic" in error
            assert all(entry["id"] in error for entry in list_topic_index())
    finally:
        builder_mcp_user.reset(token)


@pytest.mark.asyncio
async def test_topic_changes_reach_mcp_builder_relay_and_inline_builder(monkeypatch):
    topic = get_topic("common_guidelines")
    monkeypatch.setattr(topic, "content", "Updated canonical global template.")
    token = builder_mcp_user.set(SimpleNamespace(id=3, selected_organization_id=27))
    try:
        async with Client(mcp) as client:
            for arguments in ({}, {"topic": topic.id}):
                external = (
                    await client.call_tool("get_voice_prompting_guide", arguments)
                ).data
                builder = await call_builder_tool(
                    client, "get_voice_prompting_guide", arguments
                )
                assert external == builder
                assert topic.content in builder["content"]
        instructions = builder_instructions("build")
        assert render_guide() in instructions
        assert instructions.count(topic.content) == 1
        # Edits fetch only what they need; other stages do not carry the guide.
        for stage, iteration in (
            ("build_requirements", False),
            ("build", True),
        ):
            assert topic.content not in builder_instructions(stage, iteration=iteration)
    finally:
        builder_mcp_user.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", [None, "tool_calls"])
async def test_saved_builder_guide_requests_accept_legacy_arguments(topic):
    arguments = {"stage": "create", "node_type": "agentNode"}
    if topic is not None:
        arguments["topic"] = topic
    original = arguments.copy()
    token = builder_mcp_user.set(SimpleNamespace(id=3, selected_organization_id=27))
    try:
        async with Client(mcp) as client:
            result = await call_builder_tool(
                client, "get_voice_prompting_guide", arguments
            )
        assert result == {"content": render_guide(topic)}
        assert arguments == original
    finally:
        builder_mcp_user.reset(token)
