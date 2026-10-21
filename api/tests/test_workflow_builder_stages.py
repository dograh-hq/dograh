"""Behavioral checks for stage isolation, handoffs, and proposal gating."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.services.workflow.builder_runtime.agent import ITERATION_MCP_TOOLS, build_agent
from api.services.workflow.builder_runtime.schemas import BuilderStep
from api.services.workflow.builder_runtime.service import run_step
from api.tests.support.workflow_authoring import BRIEF, PLAN, READY_REVIEW
from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

QUESTION = {
    "id": "policy",
    "title": "What should happen when the lookup fails?",
    "kind": "text",
}


def brief_call(brief=None, call_id="brief"):
    return tool_call("submit_brief", {"brief": brief or BRIEF}, call_id)


def plan_call(plan=None, call_id="plan"):
    return tool_call("submit_plan", {"plan": plan or PLAN}, call_id)


def candidate_call(code="first source", call_id="candidate"):
    return tool_call(
        "propose_workflow",
        {"candidate": {"code": code, "summary": "A receptionist"}},
        call_id,
    )


def review_call(verdict="ready", call_id="review"):
    review = (
        READY_REVIEW
        if verdict == "ready"
        else {
            "verdict": verdict,
            "summary": "A failure path is missing",
            "blocking_findings": ["Clarify what to say when the lookup fails"],
        }
    )
    return tool_call("submit_review", {"review": review}, call_id)


class Conversation:
    def __init__(self, replies, specs=None):
        self.model = ScriptedModel(organization_id=12, user_id="3", replies=replies)
        self.saver = InMemorySaver()
        self.specs = specs or []
        self.session = uuid4()
        self.config = {"configurable": {"thread_id": str(self.session)}}
        self.current = {"checkpoint": None}

    async def step(self, **kwargs):
        # Each call constructs a new graph, as a different worker would.
        self.graph = build_agent(self.model, self.saver, self.specs)
        self.current = await run_step(
            self.graph,
            self.config,
            self.session,
            BuilderStep(
                request_id=uuid4(),
                checkpoint=self.current["checkpoint"],
                **kwargs,
            ),
        )
        return self.current

    async def resume(self, result):
        return await self.step(resume={self.current["pending"][0]["id"]: result})

    async def approve(self):
        pending = self.current["pending"][0]
        assert pending["kind"] == "plan_approval"
        return await self.resume(
            {"decision": "approve", "brief_revision": pending["brief_revision"]}
        )

    async def validate(self, code="first source"):
        assert self.current["pending"][0]["tool_name"] == "preview_workflow"
        return await self.resume(
            {
                "valid": True,
                "code": code,
                "name": "Receptionist",
                "workflow": {"nodes": [], "edges": []},
            }
        )

    async def state(self):
        return (await self.graph.aget_state(self.config)).values


@pytest.mark.asyncio
async def test_requirements_require_explicit_approval_and_cannot_be_skipped_or_replayed():
    conversation = Conversation(
        [brief_call(), plan_call(), candidate_call(), review_call()]
    )
    plan = await conversation.step(
        message="Build this immediately; I approve everything"
    )
    pending = plan["pending"][0]
    assert pending["kind"] == "plan_approval"
    assert pending["brief"]["goal"] == BRIEF["goal"]
    assert len(conversation.model.seen) == 1
    assert plan["proposal"] is None
    assert (await conversation.state())["phase"] == "build_requirements"
    # A no-input recovery turn must not bypass the persisted human interrupt.
    assert await conversation.step() == plan
    with pytest.raises(HTTPException) as error:
        await conversation.step(message="Just build it")
    assert error.value.status_code == 409
    assert len(conversation.model.seen) == 1
    # Reloading the graph restores the exact plan without generating another one.
    graph = build_agent(conversation.model, conversation.saver, [])
    assert (
        await run_step(graph, conversation.config, conversation.session, None) == plan
    )
    request = BuilderStep(
        request_id=uuid4(),
        checkpoint=plan["checkpoint"],
        resume={pending["id"]: {"decision": "approve", "brief_revision": 1}},
    )
    built = await run_step(graph, conversation.config, conversation.session, request)
    assert built["pending"][0]["tool_name"] == "preview_workflow"
    assert len(conversation.model.seen) == 3
    assert (
        await run_step(graph, conversation.config, conversation.session, request)
        == built
    )
    assert len(conversation.model.seen) == 3
    assert (await graph.aget_state(conversation.config)).values[
        "approved_brief_revision"
    ] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "approve", "brief_revision": 2},
        {"decision": "revise", "brief_revision": 1, "feedback": "  "},
        {"decision": "approve", "brief_revision": 1, "brief": BRIEF},
        {
            "decision": "approve",
            "brief_revision": 1,
            "feedback": "Actually make it outbound",
        },
        {"decision": "approve"},
        {"valid": True},
    ],
)
async def test_invalid_plan_decisions_do_not_resume_or_modify_the_checkpoint(decision):
    conversation = Conversation([brief_call(), plan_call(), candidate_call()])
    plan = await conversation.step(message="Build a receptionist")
    with pytest.raises(HTTPException) as error:
        await conversation.resume(decision)
    assert error.value.status_code == 422
    assert (
        await run_step(
            conversation.graph, conversation.config, conversation.session, None
        )
        == plan
    )
    assert len(conversation.model.seen) == 1


@pytest.mark.asyncio
async def test_request_changes_returns_feedback_to_planner_and_requires_new_approval():
    updated = {**BRIEF, "direction": "outbound"}
    conversation = Conversation(
        [
            brief_call(),
            brief_call(updated, "brief-2"),
            plan_call(call_id="plan-2"),
            candidate_call(),
            review_call(),
        ]
    )
    first = await conversation.step(message="Build a receptionist")
    second = await conversation.resume(
        {
            "decision": "revise",
            "brief_revision": 1,
            "feedback": "Make it outbound",
        }
    )
    assert second["pending"][0]["kind"] == "plan_approval"
    assert second["pending"][0]["brief_revision"] == 2
    assert second["pending"][0]["brief"]["direction"] == "outbound"
    assert "Make it outbound" in str(conversation.model.seen[-1])
    assert all(
        {t["function"]["name"] for t in tools} == {"ask_questions", "submit_brief"}
        for tools in conversation.model.seen_tools
    )
    assert not (await conversation.state())["approved_brief_revision"]
    # Both an old interrupt ID and an old browser checkpoint are rejected.
    with pytest.raises(HTTPException) as error:
        await conversation.step(
            resume={
                first["pending"][0]["id"]: {"decision": "approve", "brief_revision": 1}
            }
        )
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await run_step(
            conversation.graph,
            conversation.config,
            conversation.session,
            BuilderStep(
                request_id=uuid4(),
                checkpoint=first["checkpoint"],
                resume={
                    second["pending"][0]["id"]: {
                        "decision": "approve",
                        "brief_revision": 2,
                    }
                },
            ),
        )
    assert error.value.status_code == 409
    await conversation.approve()
    assert (await conversation.state())["approved_brief_revision"] == 2
    assert '"direction": "outbound"' in conversation.model.seen[-1][1].content
    assert (await conversation.validate())["proposal"]


@pytest.mark.asyncio
async def test_requirements_cannot_use_build_tools():
    conversation = Conversation(
        [
            tool_call("list_node_types", {}, "catalog"),
            candidate_call(),
            review_call(),
            tool_call("ask_questions", {"questions": [QUESTION]}, "ask"),
        ],
        [
            {
                "name": "list_node_types",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    first = await conversation.step(message="Make a receptionist")
    assert first["pending"][0]["kind"] == "questions"
    assert first["proposal"] is None
    assert not (await conversation.state()).get("brief")
    errors = [m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage)]
    assert len(errors) == 3
    assert all(m.status == "error" for m in errors)
    for messages, tools in zip(conversation.model.seen, conversation.model.seen_tools):
        assert "@dograh/sdk" not in str(messages)
        assert {t["function"]["name"] for t in tools} == {
            "ask_questions",
            "submit_brief",
        }


@pytest.mark.asyncio
async def test_reviewer_gets_artifacts_without_other_agents_private_history():
    conversation = Conversation(
        [
            AIMessage(
                content="REQUIREMENTS_PRIVATE_EXPLANATION",
                tool_calls=[
                    {
                        "name": "submit_brief",
                        "args": {"brief": BRIEF},
                        "id": "brief",
                        "type": "tool_call",
                    }
                ],
            ),
            tool_call("get_voice_prompting_guide", {}, "guide"),
            plan_call(),
            AIMessage(
                content="BUILDER_PRIVATE_EXPLANATION",
                tool_calls=candidate_call().tool_calls,
            ),
            candidate_call("unauthorized rewrite", "forbidden"),
            review_call(),
        ],
        [
            {
                "name": "get_voice_prompting_guide",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    await conversation.resume({"private_guide_marker": "PLANNER_GUIDE_ONLY"})
    assert conversation.current["proposal"] is None
    final = await conversation.validate()
    assert final["proposal"]["code"] == "first source"
    build_messages = str(conversation.model.seen[3])
    assert "REQUIREMENTS_PRIVATE_EXPLANATION" not in build_messages
    reviewer_messages = str(conversation.model.seen[-1])
    for marker in (
        "REQUIREMENTS_PRIVATE_EXPLANATION",
        "BUILDER_PRIVATE_EXPLANATION",
        "PLANNER_GUIDE_ONLY",
    ):
        assert marker not in reviewer_messages
    assert BRIEF["goal"] in reviewer_messages
    assert "first source" in reviewer_messages
    error = next(m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage))
    assert error.status == "error"  # Reviewer cannot change the candidate.
    assert all(
        "BUILDER_PRIVATE_EXPLANATION" not in m["content"] for m in final["messages"]
    )


@pytest.mark.asyncio
async def test_review_findings_force_revalidation_and_fresh_review():
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            candidate_call(),
            review_call("revise"),
            candidate_call("revised source", "candidate-2"),
            review_call(call_id="review-2"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    second = await conversation.validate()
    assert second["proposal"] is None
    assert (await conversation.state())["review"]["verdict"] == "revise"
    final = await conversation.validate("revised source")
    state = await conversation.state()
    assert final["proposal"]["code"] == "revised source"
    assert state["review"]["candidate_revision"] == state["candidate_revision"] == 2
    assert "first source" not in str(conversation.model.seen[-1])
    assert "revise" not in str(conversation.model.seen[-1][1].content).replace(
        "revised source", ""
    )


@pytest.mark.asyncio
async def test_business_gap_returns_to_planner_and_resumes_with_new_brief():
    updated = {**BRIEF, "business_rules": ["Offer a callback when the lookup fails"]}
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            candidate_call(),
            review_call("needs_input"),
            tool_call("ask_questions", {"questions": [QUESTION]}, "ask"),
            brief_call(updated, "brief-2"),
            plan_call(call_id="plan-2"),
            candidate_call("updated source", "candidate-2"),
            review_call(call_id="review-2"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    question = await conversation.validate()
    assert question["pending"][0]["kind"] == "questions"
    assert question["proposal"] is None
    assert (await conversation.state())["phase"] == "build_requirements"
    await conversation.resume({"policy": ["Offer a callback"]})
    await conversation.approve()
    state = await conversation.state()
    assert state["brief_revision"] == 2
    assert state["review"] is None
    final = await conversation.validate("updated source")
    assert final["proposal"]["brief_revision"] == 2
    assert (await conversation.state())["review"]["brief_revision"] == 2


@pytest.mark.asyncio
async def test_user_revision_invalidates_proposal_and_preserves_source_across_replanning():
    changed = {**BRIEF, "direction": "outbound"}
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            candidate_call(),
            review_call(),
            tool_call("ask_questions", {"questions": [QUESTION]}, "question-1"),
            brief_call(changed, "brief-2"),
            plan_call(call_id="plan-2"),
            tool_call(
                "request_clarification",
                {"reason": "The requested lookup is not connected."},
                "gap",
            ),
            tool_call("ask_questions", {"questions": [QUESTION]}, "question-2"),
            brief_call(changed, "brief-3"),
            plan_call(call_id="plan-3"),
            candidate_call("outbound source", "candidate-2"),
            review_call(call_id="review-2"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    await conversation.validate()
    assert conversation.current["proposal"]
    changed_state = await conversation.step(message="Make it outbound")
    assert changed_state["proposal"] is None
    assert (await conversation.state())["review"] is None
    await conversation.resume({"policy": ["Offer a callback"]})
    await conversation.approve()
    assert conversation.current["pending"][0]["kind"] == "questions"
    await conversation.resume(
        {"policy": ["Collect the request without a lookup for now"]}
    )
    await conversation.approve()
    assert "first source" in conversation.model.seen[-1][1].content
    final = await conversation.validate("outbound source")
    assert final["proposal"]["brief_revision"] == 3
    assert (await conversation.state())["brief"]["direction"] == "outbound"


@pytest.mark.asyncio
async def test_validation_failure_never_reaches_review_and_stops_after_three_attempts():
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            *[candidate_call(call_id=f"candidate-{i}") for i in range(3)],
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    for _ in range(3):
        assert conversation.current["proposal"] is None
        await conversation.resume({"valid": False, "error": "Invalid source"})
    assert not conversation.current["pending"]
    assert not conversation.current["can_continue"]
    assert conversation.current["proposal"] is None
    assert "three attempts" in conversation.current["messages"][-1]["content"]
    assert not (await conversation.state()).get("review")
    assert not conversation.model.replies


@pytest.mark.asyncio
async def test_repeated_review_failures_never_expose_a_proposal():
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            *[
                call
                for i in range(3)
                for call in (
                    candidate_call(call_id=f"candidate-{i}"),
                    review_call("revise", f"review-{i}"),
                )
            ],
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    for _ in range(3):
        await conversation.validate()
        assert conversation.current["proposal"] is None
    assert not conversation.current["can_continue"]
    assert "three reviews" in conversation.current["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_inconsistent_review_verdict_is_rejected_before_exposing_proposal():
    conversation = Conversation(
        [
            brief_call(),
            plan_call(),
            candidate_call(),
            tool_call(
                "submit_review",
                {
                    "review": {
                        **READY_REVIEW,
                        "blocking_findings": ["A required behavior is absent"],
                    }
                },
                "invalid-review",
            ),
            review_call(),
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    await conversation.validate()
    error = next(m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage))
    assert error.status == "error"
    assert "Ready requires no blockers" in error.content
    assert conversation.current["proposal"]


@pytest.mark.asyncio
async def test_parallel_handoffs_are_rejected_without_executing_either_tool():
    conversation = Conversation(
        [
            AIMessage(
                content="",
                tool_calls=[*brief_call().tool_calls, *candidate_call().tool_calls],
            ),
            tool_call("ask_questions", {"questions": [QUESTION]}, "ask"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    assert conversation.current["pending"][0]["kind"] == "questions"
    state = await conversation.state()
    assert not state.get("brief") and not state.get("candidate")
    errors = [m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage)]
    assert len(errors) == 2 and all(m.status == "error" for m in errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", [False, True])
async def test_planning_never_gets_resource_tools_and_build_only_gets_them_for_iterations(
    iteration,
):
    specs = [
        {"name": name, "inputSchema": {"type": "object", "properties": {}}}
        for name in [*ITERATION_MCP_TOOLS, "list_node_types"]
    ]
    conversation = Conversation(
        [
            brief_call(),
            tool_call("list_documents", {}, "plan-catalog"),
            plan_call(),
            tool_call("list_documents", {}, "build-catalog"),
            candidate_call(),
            review_call(),
        ],
        specs,
    )
    await conversation.step(
        message="Attach the requested knowledge source"
        if iteration
        else "Build a receptionist",
        current_workflow={"id": 41, "revision": "saved", "code": "current source"}
        if iteration
        else None,
    )
    await conversation.approve()
    assert all(
        "list_documents" not in {t["function"]["name"] for t in tools}
        for tools in conversation.model.seen_tools[:3]
    )
    plan_error = next(
        m for m in conversation.model.seen[2] if isinstance(m, ToolMessage)
    )
    assert plan_error.status == "error"
    if iteration:
        assert conversation.current["pending"][0]["tool_name"] == "list_documents"
        await conversation.resume([])
    else:
        build_error = next(
            m for m in conversation.model.seen[-1] if isinstance(m, ToolMessage)
        )
        assert build_error.status == "error"
    assert conversation.current["pending"][0]["tool_name"] == "preview_workflow"
    assert (await conversation.validate())["proposal"]
    assert all(
        "list_node_types" not in {t["function"]["name"] for t in tools}
        for tools in conversation.model.seen_tools
    )


@pytest.mark.asyncio
async def test_plan_prompts_and_deferred_connections_reach_builder_review_and_preview():
    gap = "Connect the calendar before confirming bookings."
    conversation = Conversation(
        [
            brief_call(),
            plan_call({**PLAN, "configuration_gaps": [gap]}),
            candidate_call(),
            review_call(),
        ]
    )
    await conversation.step(message="Collect appointment requests")
    await conversation.approve()
    assert PLAN["content"] in conversation.model.seen[-1][1].content
    final = await conversation.validate()
    assert PLAN["content"] in conversation.model.seen[-1][1].content
    assert gap in final["proposal"]["summary"]
    assert final["proposal"]["configuration_gaps"] == [gap]
    # Only requirements and the final preview reach the public conversation.
    assert all(PLAN["content"] not in m["content"] for m in final["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_kind", ["questions", "plan_approval"])
async def test_pre_refactor_requirements_interrupt_resumes_into_new_planning_stage(
    pending_kind,
):
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode

    from api.services.workflow.builder_runtime.agent import (
        BuilderState,
        ask_questions,
        submit_brief,
    )
    from api.services.workflow.builder_runtime.service import present

    # The former requirements agent used these node/channel names and phase.
    graph = StateGraph(BuilderState)
    reply = (
        tool_call("ask_questions", {"questions": [QUESTION]}, "ask")
        if pending_kind == "questions"
        else brief_call({**BRIEF, "open_questions": []})
    )
    graph.add_node(
        "plan", lambda state: {"plan_messages": [reply], "messages": [reply]}
    )
    graph.add_node(
        "plan_tools",
        ToolNode([ask_questions, submit_brief], messages_key="plan_messages"),
    )
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "plan_tools")
    graph.add_edge("plan_tools", END)
    conversation = Conversation(
        ([brief_call()] if pending_kind == "questions" else [])
        + [plan_call(), candidate_call(), review_call()]
    )
    legacy = graph.compile(checkpointer=conversation.saver)
    request = HumanMessage(content="Build a receptionist")
    await legacy.ainvoke(
        {"phase": "plan", "plan_messages": [request], "messages": [request]},
        conversation.config,
    )
    conversation.current = present(
        await legacy.aget_state(conversation.config), conversation.session
    )
    assert conversation.current["pending"][0]["kind"] == pending_kind
    if pending_kind == "questions":
        await conversation.resume({"policy": ["Collect a callback request"]})
        assert conversation.current["pending"][0]["kind"] == "plan_approval"
    await conversation.approve()
    assert conversation.current["pending"][0]["tool_name"] == "preview_workflow"
    assert (await conversation.state())["conversation_plan"]["content"] == PLAN[
        "content"
    ]
    assert "open_questions" not in (await conversation.state())["brief"]
    assert (await conversation.validate())["proposal"]


@pytest.mark.asyncio
async def test_pre_refactor_validation_interrupt_replans_without_losing_approved_source():
    from langchain_core.tools import tool
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import interrupt

    from api.services.workflow.authoring.contracts import BuildResult
    from api.services.workflow.builder_runtime.agent import BuilderState
    from api.services.workflow.builder_runtime.service import present

    @tool("propose_workflow")
    def legacy_proposal(candidate: BuildResult):
        """The old builder validated source without a conversation-plan handoff."""
        return interrupt(
            {
                "kind": "mcp",
                "tool_name": "preview_workflow",
                "arguments": {"code": candidate.code},
            }
        )

    graph = StateGraph(BuilderState)
    graph.add_node("build", lambda state: {"build_messages": [candidate_call()]})
    graph.add_node(
        "build_tools", ToolNode([legacy_proposal], messages_key="build_messages")
    )
    graph.add_edge(START, "build")
    graph.add_edge("build", "build_tools")
    graph.add_edge("build_tools", END)
    conversation = Conversation([plan_call(), candidate_call(), review_call()])
    legacy = graph.compile(checkpointer=conversation.saver)
    await legacy.ainvoke(
        {
            "phase": "build",
            "brief": BRIEF,
            "brief_revision": 1,
            "approved_brief_revision": 1,
        },
        conversation.config,
    )
    conversation.current = present(
        await legacy.aget_state(conversation.config), conversation.session
    )
    await conversation.validate()
    # The resumed proposal now goes through planning and fresh validation.
    assert "first source" in conversation.model.seen[0][1].content
    assert conversation.current["pending"][0]["tool_name"] == "preview_workflow"
    assert (await conversation.state())["approved_brief_revision"] == 1
    assert (await conversation.validate())["proposal"]
