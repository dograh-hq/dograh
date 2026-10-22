"""Requirements, planning, and SDK building with explicit handoffs.

Each agent has a private message channel and an independently bound tool set.
Only requirements conversations and validated proposals reach the public transcript.
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
from langgraph.types import Command, Send, interrupt
from pydantic import Field
from typing_extensions import TypedDict

from api.services.workflow.authoring import authoring_stage
from api.services.workflow.authoring.contracts import (
    AuthoringStage,
    BuildResult,
    CallBrief,
    ConversationPlan,
)

from .schemas import (
    BuilderQuestion,
    QuestionBatch,
    validate_answers,
    validate_plan_decision,
)

ASSETS = Path(__file__).parent / "assets"
STAGE_MCP_TOOLS = {
    "build_requirements": frozenset(),
    "plan": frozenset({"get_voice_prompting_guide"}),
    "build": frozenset(),
}
ITERATION_MCP_TOOLS = frozenset(
    {
        "get_node_type",
        "list_tools",
        "list_documents",
        "list_credentials",
        "list_recordings",
    }
)
ALLOWED_TOOLS = (
    frozenset.union(*STAGE_MCP_TOOLS.values())
    | ITERATION_MCP_TOOLS
    | {"preview_workflow"}
)
MAX_VALIDATION_ATTEMPTS = 3

# Keep requirements node/channel names already stored in checkpoints. Prompts,
# tool permissions, and tracing use the new stage terminology.
_GRAPH_NAMES = {
    "build_requirements": "plan",
    "plan": "plan_conversation",
    "build": "build",
    "review": "review",  # Resume checkpoints created before review was removed.
}


class BuilderState(TypedDict, total=False):
    workflow_id: int | None
    workflow_revision: str | None
    messages: Annotated[list[AnyMessage], add_messages]
    plan_conversation_messages: Annotated[list[AnyMessage], add_messages]
    plan_messages: Annotated[list[AnyMessage], add_messages]
    build_messages: Annotated[list[AnyMessage], add_messages]
    review_messages: Annotated[list[AnyMessage], add_messages]
    phase: Literal["build_requirements", "plan", "build", "review", "complete"]
    brief: dict[str, Any] | None
    brief_revision: int
    approved_brief_revision: int | None
    conversation_plan: dict[str, Any] | None
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
            f"{_GRAPH_NAMES[stage]}_messages": [
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
    return Command(
        update={
            "plan_messages": [message],
            "messages": [message],
            "phase": "build_requirements",
        }
    )


@tool
def submit_brief(
    brief: CallBrief,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Show the complete business requirements for manual approval before planning.

    The user can approve it or request changes. Never assume approval from chat.
    """
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
            "build_requirements",
            tool_call_id,
            {
                "approved": False,
                "brief_revision": revision,
                "feedback": decision.feedback,
            },
            phase="build_requirements",
            brief=brief.model_dump(),
            brief_revision=revision,
            approved_brief_revision=None,
            conversation_plan=None,
            previous_code=previous_code,
            candidate=None,
            review=None,
            proposal=None,
            messages=[
                HumanMessage(
                    content=f"Changes requested to requirements {revision}: {decision.feedback}"
                )
            ],
        )
    latest_request = next(
        (m.content for m in reversed(state.get("messages", [])) if m.type == "human"),
        "",
    )
    return _result(
        "build_requirements",
        tool_call_id,
        {"approved": True, "brief_revision": revision},
        phase="plan",
        brief=brief.model_dump(),
        brief_revision=revision,
        approved_brief_revision=revision,
        conversation_plan=None,
        previous_code=previous_code,
        candidate=None,
        review=None,
        proposal=None,
        validation_attempts=0,
        review_attempts=0,
        messages=[
            HumanMessage(content=f"Approved requirements {revision}. Build the agent.")
        ],
        plan_conversation_messages=_fresh_messages(
            {
                "brief": brief.model_dump(),
                "brief_revision": revision,
                "user_request": latest_request,
                "previous_code": previous_code,
            }
        ),
        build_messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES)],
        review_messages=[RemoveMessage(id=REMOVE_ALL_MESSAGES)],
    )


@tool
def submit_plan(
    plan: ConversationPlan,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Hand conversation flow and complete prompts to the SDK Builder.

    Requirements must already be approved. This internal handoff does not ask
    for a second approval, validate source, or show a proposal.
    """
    if not state.get("brief") or state.get("approved_brief_revision") != state.get(
        "brief_revision"
    ):
        return _result(
            "plan", tool_call_id, {"error": "Approved requirements are required."}
        )
    planned = {**plan.model_dump(), "brief_revision": state["brief_revision"]}
    return _result(
        "plan",
        tool_call_id,
        {"sent_to_builder": True},
        phase="build",
        conversation_plan=planned,
        build_messages=_fresh_messages(
            {
                "brief": state["brief"],
                "conversation_plan": planned,
                "previous_code": state.get("previous_code"),
            }
        ),
    )


def _requirements_gap(
    stage: str, call_id: str, state: dict, reason: str, **updates
) -> Command:
    return _result(
        stage,
        call_id,
        {"returned_to_requirements": True},
        phase="build_requirements",
        approved_brief_revision=None,
        conversation_plan=None,
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
    """Return a missing business decision to the Requirements agent.

    Explain the gap and business alternatives. Do not silently omit a requirement.
    """
    return _requirements_gap(state["phase"], tool_call_id, state, reason, review=None)


@tool
def propose_workflow(
    candidate: BuildResult,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Validate complete workflow source and show the preview to the user.

    Fix validation errors and retry. Successful validation finishes the turn
    and exposes the proposal. Saving remains a separate user action.
    """
    if not state.get("brief") or state.get("approved_brief_revision") != state.get(
        "brief_revision"
    ):
        return _result(
            "build",
            tool_call_id,
            {"error": "A human-approved business brief is required."},
        )
    plan = state.get("conversation_plan")
    if not plan or plan.get("brief_revision") != state["brief_revision"]:
        # A pre-refactor checkpoint may resume without the new conversation plan.
        return _result(
            "build",
            tool_call_id,
            {"sent_for_planning": True},
            phase="plan",
            previous_code=candidate.code,
            plan_conversation_messages=_fresh_messages(
                {
                    "brief": state["brief"],
                    "previous_code": candidate.code,
                    "configuration_gaps": candidate.configuration_gaps,
                }
            ),
        )
    # Preserve explicitly deferred work even if the SDK writer omits it.
    candidate = candidate.model_copy(
        update={
            "configuration_gaps": list(
                dict.fromkeys(
                    [*plan["configuration_gaps"], *candidate.configuration_gaps]
                )
            )
        }
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
        {"valid": True},
        candidate=validated,
        candidate_revision=revision,
        validation_attempts=0,
        **_proposal_updates(validated),
    )


def _proposal_updates(candidate: dict) -> dict:
    summary = candidate["summary"]
    if candidate["configuration_gaps"]:
        summary += "\n\nStill to configure:\n" + "\n".join(
            f"- {gap}" for gap in candidate["configuration_gaps"]
        )
    return {
        "phase": "complete",
        "review": None,
        "proposal": {**candidate, "summary": summary},
        "messages": [AIMessage(content=summary)],
    }


def _finish_legacy_review(state: BuilderState) -> dict:
    """Finish previously validated candidates when resuming an old checkpoint."""
    candidate = state.get("candidate")
    if (
        not candidate
        or not candidate.get("valid")
        or not state.get("brief")
        or candidate.get("brief_revision") != state.get("brief_revision")
        or state.get("approved_brief_revision") != state.get("brief_revision")
    ):
        return {"phase": "build", "review": None, "proposal": None}
    return _proposal_updates(candidate)


LOCAL_TOOLS = {
    "build_requirements": [ask_questions, submit_brief],
    "plan": [request_clarification, submit_plan],
    "build": [request_clarification, propose_workflow],
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


def builder_instructions(
    stage: AuthoringStage | str = AuthoringStage.build_requirements,
    *,
    iteration: bool = False,
) -> str:
    stage = AuthoringStage(stage)
    if stage.value not in STAGE_MCP_TOOLS:
        raise ValueError(f"Unsupported in-app builder stage: {stage.value}")
    return "\n\n".join(
        [
            authoring_stage(stage, iteration=iteration)["instructions"],
            (ASSETS / "instructions.md").read_text(encoding="utf-8").strip(),
            (ASSETS / f"{stage.value}.md").read_text(encoding="utf-8").strip(),
        ]
    )


def build_agent(model, checkpointer, tool_specs: list[dict]):
    graph = StateGraph(BuilderState)

    def route(state):
        phase = state.get("phase", "build_requirements")
        return END if phase == "complete" else _GRAPH_NAMES[phase]

    def add_stage(stage):
        node_name = _GRAPH_NAMES[stage]
        key = f"{node_name}_messages"

        def stage_mcp_tools(state):
            allowed = STAGE_MCP_TOOLS[stage]
            if state.get("previous_code") and stage == "build":
                allowed = allowed | ITERATION_MCP_TOOLS
            return allowed

        def stage_tools(state):
            return [
                *LOCAL_TOOLS[stage],
                *[
                    relay_tool(spec)
                    for spec in tool_specs
                    if spec["name"] in stage_mcp_tools(state)
                ],
            ]

        async def invoke(state: BuilderState):
            agent_model = model.bind_tools(stage_tools(state), authoring_stage=stage)
            reply = await agent_model.ainvoke(
                [
                    SystemMessage(
                        content=builder_instructions(
                            stage, iteration=bool(state.get("previous_code"))
                        )
                    ),
                    *state.get(key, []),
                ]
            )
            updates = {
                key: [reply],
                "idle_turns": 0 if reply.tool_calls else state.get("idle_turns", 0) + 1,
            }
            if stage == "build_requirements":
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
                # Read batches are split into separate tasks by after_model.
                # Questions and stage handoffs must run on their own.
                return {
                    key: [
                        ToolMessage(
                            content=(
                                "Only independent read tools can run in parallel. "
                                "Call questions and stage handoffs on their own."
                            ),
                            tool_call_id=call["id"],
                            status="error",
                        )
                        for call in calls
                    ]
                }
            return await ToolNode(stage_tools(state), messages_key=key).ainvoke(
                state, config
            )

        def after_model(state):
            if state.get("phase") == "complete":
                return END
            reply = state[key][-1]
            calls = getattr(reply, "tool_calls", [])
            read_tools = stage_mcp_tools(state) & {spec["name"] for spec in tool_specs}
            if len(calls) > 1 and all(call["name"] in read_tools for call in calls):
                # Each read needs its own task/interrupt ID so results survive
                # checkpoint reloads and resume against the correct tool call.
                return [
                    Send(
                        f"{node_name}_tools",
                        {
                            "previous_code": state.get("previous_code"),
                            key: [AIMessage(content="", tool_calls=[call])],
                        },
                    )
                    for call in calls
                ]
            if calls:
                return f"{node_name}_tools"
            return END if stage == "build_requirements" else node_name

        graph.add_node(node_name, invoke)
        graph.add_node(f"{node_name}_tools", execute)
        graph.add_conditional_edges(node_name, after_model)
        graph.add_conditional_edges(f"{node_name}_tools", route)

    for stage in STAGE_MCP_TOOLS:
        add_stage(stage)
    # Old checkpoints can be paused before review or inside its read tools.
    # Drain those tasks, then expose the validated candidate without inference.
    graph.add_node("review_tools", lambda state: {})
    graph.add_edge("review_tools", "review")
    graph.add_node("review", _finish_legacy_review)
    graph.add_conditional_edges("review", route)
    graph.add_conditional_edges(START, route)
    return graph.compile(checkpointer=checkpointer)
