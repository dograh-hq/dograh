"""Three isolated agents in one checkpointed graph, with explicit handoffs.

Each agent has a private message channel and an independently bound tool set.
Only the Planner's conversation and reviewed proposals reach the public transcript.
The flat graph keeps interrupts and checkpoints compatible with the existing relay.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import InjectedToolCallId, StructuredTool, tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.types import Command, interrupt
from pydantic import Field
from typing_extensions import TypedDict

from api.services.workflow.authoring import authoring_stage
from api.services.workflow.authoring.contracts import (
    AuthoringStage,
    BuildResult,
    CallBrief,
    ReviewResult,
)

from .schemas import (
    BuilderQuestion,
    QuestionBatch,
    validate_answers,
    validate_plan_decision,
)

ASSETS = Path(__file__).parent / "assets"
STAGE_MCP_TOOLS = {
    "plan": frozenset(),
    "build": frozenset(
        {
            "list_node_types",
            "get_node_type",
            "list_tools",
            "list_documents",
            "list_credentials",
            "list_recordings",
            "get_voice_prompting_guide",
        }
    ),
    "review": frozenset(
        {
            "get_node_type",
            "list_tools",
            "list_documents",
            "list_credentials",
            "list_recordings",
            "get_voice_prompting_guide",
        }
    ),
}
ALLOWED_TOOLS = frozenset.union(*STAGE_MCP_TOOLS.values()) | {"preview_workflow"}
MAX_VALIDATION_ATTEMPTS = 3
MAX_REVIEW_ATTEMPTS = 3


class BuilderState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    plan_messages: Annotated[list[AnyMessage], add_messages]
    build_messages: Annotated[list[AnyMessage], add_messages]
    review_messages: Annotated[list[AnyMessage], add_messages]
    phase: Literal["plan", "build", "review", "complete"]
    brief: dict[str, Any] | None
    brief_revision: int
    approved_brief_revision: int | None
    candidate: dict[str, Any] | None
    candidate_revision: int
    previous_code: str | None
    review: dict[str, Any] | None
    proposal: dict[str, Any] | None
    validation_attempts: int
    review_attempts: int
    idle_turns: int
    last_request_id: str


def _fresh_messages(payload: dict) -> list[AnyMessage]:
    return [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        HumanMessage(content=json.dumps(payload)),
    ]


def _result(stage: str, call_id: str, payload: dict, **updates) -> Command:
    return Command(
        update={
            f"{stage}_messages": [
                ToolMessage(content=json.dumps(payload), tool_call_id=call_id)
            ],
            **updates,
        }
    )


def _stop(stage: str, call_id: str, message: str, **updates) -> Command:
    return _result(
        stage,
        call_id,
        {"error": message},
        phase="complete",
        proposal=None,
        messages=[AIMessage(content=message)],
        **updates,
    )


@tool
def ask_questions(
    questions: Annotated[list[BuilderQuestion], Field(min_length=1, max_length=3)],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Ask up to three missing business requirements using interactive cards.

    Use single for one choice, multiple for several values, text for free text.
    Do not ask again about requirements already supplied by the user.
    """
    payload = QuestionBatch(questions=questions).model_dump()
    answers = interrupt({"kind": "questions", **payload})
    validated = validate_answers(payload["questions"], answers)
    message = ToolMessage(content=json.dumps(validated), tool_call_id=tool_call_id)
    return Command(update={"plan_messages": [message], "messages": [message]})


@tool
def submit_brief(
    brief: CallBrief,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Show the complete business plan for human approval before building.

    The user can approve it or request changes. Never assume approval from chat.
    """
    if brief.open_questions:
        return _result(
            "plan",
            tool_call_id,
            {
                "error": "Resolve the blocking questions before building.",
                "open_questions": brief.open_questions,
            },
        )
    revision = state.get("brief_revision", 0) + 1
    # Interrupt before any handoff. On resume LangGraph replays this tool with
    # the checkpointed arguments, so approval is bound to this exact brief.
    decision = validate_plan_decision(
        revision,
        interrupt(
            {
                "kind": "plan_approval",
                "brief": brief.model_dump(),
                "brief_revision": revision,
            }
        ),
    )
    previous = state.get("candidate") or state.get("proposal") or {}
    previous_code = previous.get("code") or state.get("previous_code")
    if decision.decision == "revise":
        return _result(
            "plan",
            tool_call_id,
            {
                "approved": False,
                "brief_revision": revision,
                "feedback": decision.feedback,
            },
            phase="plan",
            brief=brief.model_dump(),
            brief_revision=revision,
            approved_brief_revision=None,
            previous_code=previous_code,
            candidate=None,
            review=None,
            proposal=None,
            messages=[
                HumanMessage(
                    content=f"Changes requested to plan {revision}: {decision.feedback}"
                )
            ],
        )
    latest_request = next(
        (m.content for m in reversed(state.get("messages", [])) if m.type == "human"),
        "",
    )
    return _result(
        "plan",
        tool_call_id,
        {"approved": True, "brief_revision": revision},
        phase="build",
        brief=brief.model_dump(),
        brief_revision=revision,
        approved_brief_revision=revision,
        previous_code=previous_code,
        candidate=None,
        review=None,
        proposal=None,
        validation_attempts=0,
        review_attempts=0,
        messages=[HumanMessage(content=f"Approved plan {revision}. Build the agent.")],
        build_messages=_fresh_messages(
            {
                "brief": brief.model_dump(),
                "brief_revision": revision,
                "user_request": latest_request,
                "previous_code": previous_code,
            }
        ),
        review_messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES)],
    )


def _planning_gap(
    stage: str, call_id: str, state: dict, reason: str, **updates
) -> Command:
    return _result(
        stage,
        call_id,
        {"returned_to_planner": True},
        phase="plan",
        approved_brief_revision=None,
        proposal=None,
        plan_messages=[
            HumanMessage(
                content=json.dumps(
                    {
                        "business_question_from": stage,
                        "reason": reason,
                        "current_brief": state.get("brief"),
                    }
                )
            )
        ],
        **updates,
    )


@tool
def request_clarification(
    reason: Annotated[str, Field(min_length=1)],
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Return a missing business decision or required configuration to the Planner.

    Explain the gap and business alternatives. Do not silently omit a requirement.
    """
    return _planning_gap("build", tool_call_id, state, reason, review=None)


@tool
def propose_workflow(
    candidate: BuildResult,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Validate complete workflow source and hand it to the independent Reviewer.

    This does not display, save, or publish a proposal. Fix validation errors
    and retry. The runtime exposes a proposal only after the Reviewer accepts it.
    """
    if (
        not state.get("brief")
        or state["brief"].get("open_questions")
        or state.get("approved_brief_revision") != state.get("brief_revision")
    ):
        return _result(
            "build",
            tool_call_id,
            {"error": "A human-approved business brief is required."},
        )
    result = interrupt(
        {
            "kind": "mcp",
            "tool_name": "preview_workflow",
            "arguments": {"code": candidate.code},
        }
    )
    attempts = state.get("validation_attempts", 0) + 1
    if not result.get("valid"):
        if attempts >= MAX_VALIDATION_ATTEMPTS:
            return _stop(
                "build",
                tool_call_id,
                "I couldn't validate this workflow after three attempts. Please revise the request to continue.",
                validation_attempts=attempts,
            )
        return _result(
            "build", tool_call_id, result, validation_attempts=attempts, proposal=None
        )
    revision = state.get("candidate_revision", 0) + 1
    validated = {
        **result,
        **candidate.model_dump(),
        "brief_revision": state["brief_revision"],
        "candidate_revision": revision,
    }
    return _result(
        "build",
        tool_call_id,
        {"valid": True, "sent_for_review": True},
        phase="review",
        candidate=validated,
        candidate_revision=revision,
        review=None,
        proposal=None,
        validation_attempts=0,
        review_messages=_fresh_messages(
            {"brief": state["brief"], "candidate": validated}
        ),
    )


@tool
def submit_review(
    review: ReviewResult,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Return independent findings. Only ready exposes a proposal; other verdicts route back."""
    candidate = state.get("candidate")
    if not candidate or candidate.get("brief_revision") != state.get("brief_revision"):
        return _result(
            "review",
            tool_call_id,
            {"error": "No validated candidate for the current brief."},
        )
    result = {
        **review.model_dump(),
        "brief_revision": state["brief_revision"],
        "candidate_revision": candidate["candidate_revision"],
    }
    attempts = state.get("review_attempts", 0) + 1
    if review.verdict == "ready":
        summary = candidate["summary"]
        if candidate["configuration_gaps"]:
            summary += "\n\nStill to configure:\n" + "\n".join(
                f"- {gap}" for gap in candidate["configuration_gaps"]
            )
        if review.advisories:
            summary += "\n\nReview notes:\n" + "\n".join(
                f"- {note}" for note in review.advisories
            )
        return _result(
            "review",
            tool_call_id,
            {"ready": True},
            phase="complete",
            review=result,
            review_attempts=attempts,
            proposal={**candidate, "summary": summary},
            messages=[
                AIMessage(
                    content=summary
                    + "\n\nReview the preview and save a draft when ready."
                )
            ],
        )
    if attempts >= MAX_REVIEW_ATTEMPTS:
        return _stop(
            "review",
            tool_call_id,
            "The workflow still needs changes after three reviews:\n"
            + "\n".join(f"- {finding}" for finding in review.blocking_findings),
            review=result,
            review_attempts=attempts,
        )
    if review.verdict == "needs_input":
        return _planning_gap(
            "review",
            tool_call_id,
            state,
            "\n".join(review.blocking_findings),
            review=result,
            review_attempts=attempts,
        )
    return _result(
        "review",
        tool_call_id,
        {"returned_to_builder": True},
        phase="build",
        review=result,
        review_attempts=attempts,
        proposal=None,
        build_messages=[HumanMessage(content=json.dumps({"review_findings": result}))],
    )


LOCAL_TOOLS = {
    "plan": [ask_questions, submit_brief],
    "build": [request_clarification, propose_workflow],
    "review": [submit_review],
}


def relay_tool(spec: dict) -> StructuredTool:
    name = spec["name"]

    def call(**arguments):
        return interrupt({"kind": "mcp", "tool_name": name, "arguments": arguments})

    return StructuredTool.from_function(
        call,
        name=name,
        description=spec.get("description") or name,
        args_schema=spec["inputSchema"],
    )


def builder_instructions(stage: AuthoringStage | str = AuthoringStage.plan) -> str:
    stage = AuthoringStage(stage)
    return "\n\n".join(
        [
            authoring_stage(stage)["instructions"],
            (ASSETS / "instructions.md").read_text(encoding="utf-8").strip(),
            (ASSETS / f"{stage.value}.md").read_text(encoding="utf-8").strip(),
        ]
    )


def build_agent(model, checkpointer, tool_specs: list[dict]):
    graph = StateGraph(BuilderState)

    def route(state):
        phase = state.get("phase", "plan")
        return END if phase == "complete" else phase

    def add_stage(stage):
        key = f"{stage}_messages"
        tools = [
            *LOCAL_TOOLS[stage],
            *[
                relay_tool(spec)
                for spec in tool_specs
                if spec["name"] in STAGE_MCP_TOOLS[stage]
            ],
        ]
        agent_model = model.bind_tools(tools, authoring_stage=stage)
        tool_node = ToolNode(tools, messages_key=key)

        async def invoke(state: BuilderState):
            reply = await agent_model.ainvoke(
                [
                    SystemMessage(content=builder_instructions(stage)),
                    *state.get(key, []),
                ]
            )
            updates = {
                key: [reply],
                "idle_turns": 0 if reply.tool_calls else state.get("idle_turns", 0) + 1,
            }
            if stage == "plan":
                updates["messages"] = [reply]
            elif not reply.tool_calls:
                if updates["idle_turns"] >= 2:
                    updates.update(
                        phase="complete",
                        proposal=None,
                        messages=[
                            AIMessage(
                                content="I couldn't finish preparing the workflow. Send a message to continue."
                            )
                        ],
                    )
                else:
                    updates[key].append(
                        HumanMessage(
                            content="Continue using the available tools; finish with the stage's handoff tool."
                        )
                    )
            return updates

        async def execute(state: BuilderState, config):
            calls = state[key][-1].tool_calls
            if len(calls) != 1:
                # Avoid concurrent stage changes and ambiguous interrupt replay.
                return {
                    key: [
                        ToolMessage(
                            content="Call exactly one tool at a time.",
                            tool_call_id=call["id"],
                            status="error",
                        )
                        for call in calls
                    ]
                }
            return await tool_node.ainvoke(state, config)

        def after_model(state):
            if state.get("phase") == "complete":
                return END
            if getattr(state[key][-1], "tool_calls", []):
                return f"{stage}_tools"
            return END if stage == "plan" else stage

        graph.add_node(stage, invoke)
        graph.add_node(f"{stage}_tools", execute)
        graph.add_conditional_edges(stage, after_model)
        graph.add_conditional_edges(f"{stage}_tools", route)

    for stage in STAGE_MCP_TOOLS:
        add_stage(stage)
    graph.add_conditional_edges(START, route)
    return graph.compile(checkpointer=checkpointer)
