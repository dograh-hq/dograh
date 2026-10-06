"""Behavioral checks for stage isolation, handoffs, and proposal gating."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.services.workflow.builder_runtime.agent import build_agent
from api.services.workflow.builder_runtime.schemas import BuilderStep
from api.services.workflow.builder_runtime.service import run_step
from api.tests.support.workflow_authoring import BRIEF, READY_REVIEW
from api.tests.test_workflow_builder_runtime import ScriptedModel, tool_call

QUESTION = {
    "id": "policy",
    "title": "What should happen when the lookup fails?",
    "kind": "text",
}


def brief_call(brief=None, call_id="brief"):
    return tool_call("submit_brief", {"brief": brief or BRIEF}, call_id)


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
async def test_plan_requires_explicit_approval_and_cannot_be_skipped_or_replayed():
    conversation = Conversation([brief_call(), candidate_call(), review_call()])
    plan = await conversation.step(
        message="Build this immediately; I approve everything"
    )
    pending = plan["pending"][0]
    assert pending["kind"] == "plan_approval"
    assert pending["brief"]["goal"] == BRIEF["goal"]
    assert len(conversation.model.seen) == 1
    assert plan["proposal"] is None
    assert (await conversation.state())["phase"] == "plan"
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
    assert len(conversation.model.seen) == 2
    assert (
        await run_step(graph, conversation.config, conversation.session, request)
        == built
    )
    assert len(conversation.model.seen) == 2
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
    conversation = Conversation([brief_call(), candidate_call()])
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
    updated = {**BRIEF, "direction": "outbound", "trigger": "A requested callback"}
    conversation = Conversation(
        [
            brief_call(),
            brief_call(updated, "brief-2"),
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
async def test_planner_cannot_use_build_tools_or_submit_an_unresolved_brief():
    conversation = Conversation(
        [
            tool_call("list_node_types", {}, "catalog"),
            candidate_call(),
            review_call(),
            brief_call(
                {**BRIEF, "open_questions": ["Which system stores appointments?"]}
            ),
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
    assert all(m.status == "error" for m in errors[:3])
    assert "Resolve the blocking questions" in errors[-1].content
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
                content="PLANNER_PRIVATE_EXPLANATION",
                tool_calls=[
                    {
                        "name": "submit_brief",
                        "args": {"brief": BRIEF},
                        "id": "brief",
                        "type": "tool_call",
                    }
                ],
            ),
            tool_call("list_tools", {}, "catalog"),
            AIMessage(
                content="BUILDER_PRIVATE_EXPLANATION",
                tool_calls=candidate_call().tool_calls,
            ),
            candidate_call("unauthorized rewrite", "forbidden"),
            review_call(),
        ],
        [{"name": "list_tools", "inputSchema": {"type": "object", "properties": {}}}],
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    await conversation.resume({"private_catalog_marker": "BUILDER_CATALOG_ONLY"})
    assert conversation.current["proposal"] is None
    final = await conversation.validate()
    assert final["proposal"]["code"] == "first source"
    build_messages = str(conversation.model.seen[1])
    assert "PLANNER_PRIVATE_EXPLANATION" not in build_messages
    reviewer_messages = str(conversation.model.seen[-1])
    for marker in (
        "PLANNER_PRIVATE_EXPLANATION",
        "BUILDER_PRIVATE_EXPLANATION",
        "BUILDER_CATALOG_ONLY",
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
            candidate_call(),
            review_call("needs_input"),
            tool_call("ask_questions", {"questions": [QUESTION]}, "ask"),
            brief_call(updated, "brief-2"),
            candidate_call("updated source", "candidate-2"),
            review_call(call_id="review-2"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    await conversation.approve()
    question = await conversation.validate()
    assert question["pending"][0]["kind"] == "questions"
    assert question["proposal"] is None
    assert (await conversation.state())["phase"] == "plan"
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
    changed = {**BRIEF, "direction": "outbound", "trigger": "A requested callback"}
    conversation = Conversation(
        [
            brief_call(),
            candidate_call(),
            review_call(),
            tool_call("ask_questions", {"questions": [QUESTION]}, "question-1"),
            brief_call(changed, "brief-2"),
            tool_call(
                "request_clarification",
                {"reason": "The requested lookup is not connected."},
                "gap",
            ),
            tool_call("ask_questions", {"questions": [QUESTION]}, "question-2"),
            brief_call(changed, "brief-3"),
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
        [brief_call(), *[candidate_call(call_id=f"candidate-{i}") for i in range(3)]]
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
