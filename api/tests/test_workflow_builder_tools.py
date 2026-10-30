"""The tool-approval gate: creating and editing tools is committed only after
the user approves it, and the mutating tools are never agent-callable."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.services.workflow.builder_runtime.schemas import BuilderStep
from api.services.workflow.builder_runtime.service import run_step
from api.tests.support.workflow_authoring import BRIEF, READY_REVIEW
from api.tests.test_workflow_builder_runtime import (
    ScriptedModel,
    approve_plan,
    tool_call,
)

# The simplest valid tool: calculator takes no config and references nothing.
CALC_TOOL = {"name": "Calculator", "definition": {"type": "calculator"}}
PREVIEW_OK = {
    "valid": True,
    "name": "Receptionist",
    "code": "workflow source",
    "workflow": {"nodes": [], "edges": []},
}
# get_tool is the only tool-related MCP spec the build stage should bind;
# create_tool/update_tool are offered but must stay out of the agent's hands.
TOOL_SPECS = [
    {"name": name, "inputSchema": {"type": "object", "properties": {}}}
    for name in ("get_tool", "create_tool", "update_tool", "list_node_types")
]


def _step(graph, config, session_id, checkpoint, resume):
    return run_step(
        graph,
        config,
        session_id,
        BuilderStep(request_id=uuid4(), checkpoint=checkpoint, resume=resume),
    )


def _tool_messages(messages):
    return [m for m in messages if isinstance(m, ToolMessage)]


@pytest.mark.asyncio
async def test_propose_tool_commits_only_after_approval():
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call(
                "propose_tool",
                {"tool": CALC_TOOL, "plain_language": "Does arithmetic in-call."},
                "propose",
            ),
            tool_call(
                "propose_workflow",
                {"candidate": {"code": "workflow source", "summary": "Receptionist"}},
                "candidate",
            ),
            tool_call("submit_review", {"review": READY_REVIEW}, "review"),
        ],
    )
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent_memory(model, TOOL_SPECS)

    first = await run_step(
        graph, config, session_id, BuilderStep(request_id=uuid4(), message="Build it")
    )
    gated = await approve_plan(graph, config, session_id, first)

    # The build agent paused for approval before anything was created.
    approval = gated["pending"][0]
    assert approval["kind"] == "tool_approval"
    assert approval["operation"] == "create"
    assert approval["tool"]["name"] == "Calculator"
    assert approval["plain_language"]
    assert approval["used_by_count"] is None  # Deferred count; warning is text.

    # Approving replays the handoff past the gate and fires the create relay --
    # the mutation is this second, post-approval interrupt, never before.
    committed = await _step(
        graph, config, session_id, gated["checkpoint"],
        {approval["id"]: {"decision": "approve"}},
    )
    relayed = committed["pending"][0]
    assert relayed["kind"] == "mcp"
    assert relayed["tool_name"] == "create_tool"
    # propose_tool normalizes to a full CreateToolRequest (defaults filled in).
    request = relayed["arguments"]["request"]
    assert request["name"] == "Calculator"
    assert request["definition"]["type"] == "calculator"
    assert request["category"] == "calculator"

    # The relay (simulated here) returns the new uuid; it reaches the agent.
    proceeded = await _step(
        graph, config, session_id, committed["checkpoint"],
        {relayed["id"]: {"created": True, "tool_uuid": "tool-123"}},
    )
    assert proceeded["pending"][0]["tool_name"] == "preview_workflow"
    assert any(
        "tool-123" in str(m.content) for m in _tool_messages(model.seen[-1])
    )

    final = await _step(
        graph, config, session_id, proceeded["checkpoint"],
        {proceeded["pending"][0]["id"]: PREVIEW_OK},
    )
    assert final["proposal"]["name"] == "Receptionist"

    # The mutators were never offered to the model; get_tool was.
    bound = {item["function"]["name"] for tools in model.seen_tools for item in tools}
    assert "create_tool" not in bound and "update_tool" not in bound
    assert {"propose_tool", "propose_tool_edit", "get_tool"} <= bound


@pytest.mark.asyncio
async def test_denied_tool_is_not_created():
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call(
                "propose_tool",
                {"tool": CALC_TOOL, "plain_language": "Does arithmetic in-call."},
                "propose",
            ),
            # After the denial the agent moves on without the tool.
            tool_call(
                "propose_workflow",
                {"candidate": {"code": "workflow source", "summary": "Receptionist"}},
                "candidate",
            ),
            tool_call("submit_review", {"review": READY_REVIEW}, "review"),
        ],
    )
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent_memory(model, TOOL_SPECS)

    first = await run_step(
        graph, config, session_id, BuilderStep(request_id=uuid4(), message="Build it")
    )
    gated = await approve_plan(graph, config, session_id, first)
    approval = gated["pending"][0]
    assert approval["kind"] == "tool_approval"

    after_deny = await _step(
        graph, config, session_id, gated["checkpoint"],
        {approval["id"]: {"decision": "deny", "feedback": "Use a separate tool."}},
    )
    # No create relay fired -- the next pause is the workflow preview, not a
    # create_tool call. The denial and its feedback reached the agent.
    assert after_deny["pending"][0]["tool_name"] == "preview_workflow"
    denied = _tool_messages(model.seen[-1])
    assert any(
        '"approved": false' in m.content.lower()
        and "separate tool" in m.content
        for m in denied
    )


@pytest.mark.asyncio
async def test_propose_tool_edit_gates_update():
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call(
                "propose_tool_edit",
                {
                    "tool_uuid": "tool-existing",
                    "changes": {"description": "Clearer description."},
                    "plain_language": "Tighten the tool description.",
                },
                "edit",
            ),
            tool_call(
                "propose_workflow",
                {"candidate": {"code": "workflow source", "summary": "Receptionist"}},
                "candidate",
            ),
            tool_call("submit_review", {"review": READY_REVIEW}, "review"),
        ],
    )
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent_memory(model, TOOL_SPECS)

    first = await run_step(
        graph, config, session_id, BuilderStep(request_id=uuid4(), message="Edit it")
    )
    gated = await approve_plan(graph, config, session_id, first)
    approval = gated["pending"][0]
    assert approval["kind"] == "tool_approval"
    assert approval["operation"] == "update"
    assert approval["tool_uuid"] == "tool-existing"

    committed = await _step(
        graph, config, session_id, gated["checkpoint"],
        {approval["id"]: {"decision": "approve"}},
    )
    relayed = committed["pending"][0]
    assert relayed["kind"] == "mcp"
    assert relayed["tool_name"] == "update_tool"
    assert relayed["arguments"]["tool_uuid"] == "tool-existing"
    assert relayed["arguments"]["request"]["description"] == "Clearer description."


@pytest.mark.asyncio
async def test_tool_decision_validation_rejects_feedback_on_approve():
    model = ScriptedModel(
        model_name="test",
        organization_id=12,
        user_id="3",
        replies=[
            tool_call("submit_brief", {"brief": BRIEF}, "brief"),
            tool_call(
                "propose_tool",
                {"tool": CALC_TOOL, "plain_language": "Does arithmetic in-call."},
                "propose",
            ),
        ],
    )
    session_id = uuid4()
    config = {"configurable": {"thread_id": str(session_id)}}
    graph = build_agent_memory(model, TOOL_SPECS)
    first = await run_step(
        graph, config, session_id, BuilderStep(request_id=uuid4(), message="Build it")
    )
    gated = await approve_plan(graph, config, session_id, first)
    approval = gated["pending"][0]
    with pytest.raises(HTTPException) as invalid:
        await _step(
            graph, config, session_id, gated["checkpoint"],
            {approval["id"]: {"decision": "approve", "feedback": "contradiction"}},
        )
    assert invalid.value.status_code == 422


def build_agent_memory(model, specs):
    from api.services.workflow.builder_runtime.agent import build_agent

    return build_agent(model, InMemorySaver(), specs)
