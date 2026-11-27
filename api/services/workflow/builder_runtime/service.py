import asyncio
import json
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from api.db import db_client

from .agent import _fresh_messages, build_agent
from .context import INLINE_RESULT_CHARACTERS, recent_conversation
from .model import BUILDER_USAGE_CONTEXT, BuilderChatModel
from .schemas import (
    BuilderStep,
    validate_answers,
    validate_plan_decision,
    validate_tool_decision,
)
from .working_copy import source_revision


def present(snapshot, session_id: UUID) -> dict:
    messages = []
    question_calls = {}
    for message in snapshot.values.get("messages", []):
        for call in getattr(message, "tool_calls", []):
            if call["name"] == "ask_questions":
                question_calls[call["id"]] = call["args"].get("questions", [])
        if (
            message.type == "tool"
            and getattr(message, "tool_call_id", None) in question_calls
        ):
            try:
                answers = json.loads(message.content)
                content = "\n".join(
                    f"{question['title']}: {', '.join(answers[question['id']])}"
                    for question in question_calls[message.tool_call_id]
                )
                messages.append({"id": message.id, "role": "user", "content": content})
            except (ValueError, TypeError, KeyError):
                pass  # A tool error is for the agent to recover from, not a user answer.
        if message.type in ("human", "ai") and message.content:
            content = (
                message.content
                if isinstance(message.content, str)
                else "\n".join(
                    block.get("text", "")
                    for block in message.content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
            if content.strip():
                messages.append(
                    {
                        "id": message.id or str(len(messages)),
                        "role": "user" if message.type == "human" else "assistant",
                        "content": content,
                    }
                )
    pending = [
        {"id": item.id, **item.value}
        for task in snapshot.tasks
        for item in task.interrupts
    ]
    return {
        "id": str(session_id),
        "workflow_id": snapshot.values.get("workflow_id"),
        "workflow_revision": snapshot.values.get("workflow_revision"),
        "checkpoint": (snapshot.config or {})
        .get("configurable", {})
        .get("checkpoint_id"),
        "messages": messages,
        "pending": pending,
        "proposal": snapshot.values.get("proposal"),
        "can_continue": bool(snapshot.next),
        "model": BUILDER_USAGE_CONTEXT,
    }


async def run_step(graph, config: dict, session_id: UUID, request: BuilderStep | None):
    snapshot = await graph.aget_state(config)
    if request is None:
        if not snapshot.values:
            raise HTTPException(404, "Builder session not found")
        return present(snapshot, session_id)
    current = present(snapshot, session_id)
    if snapshot.values.get("last_request_id") == str(request.request_id):
        # A replayed HTTP request must not append another message or resume twice.
        if current["pending"] or not snapshot.next:
            return current
        value = None  # Recover a checkpoint interrupted by a worker failure.
    else:
        if current["checkpoint"] != request.checkpoint:
            raise HTTPException(409, "Conversation changed; reload and try again")
        updates = {"last_request_id": str(request.request_id)}
        if request.tools:
            updates["tools"] = request.tools
        if request.message is not None:
            if current["pending"]:
                raise HTTPException(
                    409,
                    "Respond to the pending questions or requirements before sending another message",
                )
            message = HumanMessage(content=request.message, id=str(request.request_id))
            previous = (
                snapshot.values.get("candidate")
                or snapshot.values.get("proposal")
                or {}
            )
            previous_code = (
                snapshot.values.get("working_code")
                or previous.get("code")
                or snapshot.values.get("previous_code")
            )
            value = {
                **updates,
                "phase": "build_requirements",
                "approved_brief_revision": None,
                "proposal": None,
                "review": None,
                "candidate": None,
                "validation_attempts": 0,
                "model_repair_attempts": 0,
                "validated_working_copy": None,
                "idle_turns": 0,
                "pending_tool_calls": [],
                "messages": [message],
                "plan_messages": [message],
            }
            if request.current_workflow is not None and (
                request.current_workflow["revision"]
                != snapshot.values.get("workflow_revision")
                or request.current_workflow["id"] != snapshot.values.get("workflow_id")
            ):
                workflow = request.current_workflow
                previous_code = workflow["code"]
                value.update(
                    workflow_id=workflow["id"],
                    workflow_revision=workflow["revision"],
                    edit_history=[],
                )
            if previous_code:
                value.update(
                    phase="build",
                    previous_code=previous_code,
                    working_code=previous_code,
                    build_messages=_fresh_messages(
                        {
                            "previous_code": previous_code
                            if len(previous_code) <= 20_000
                            else "Source is stored in the working copy. Use read_workflow to retrieve it.",
                            "working_revision": source_revision(previous_code),
                            "user_request": request.message,
                            "conversation": recent_conversation(current["messages"]),
                            "configuration_gaps": snapshot.values.get(
                                "configuration_gaps", []
                            ),
                            "recent_tool_results": [
                                {"result_id": key, "tool": result["tool"]}
                                for key, result in list(
                                    snapshot.values.get("tool_results", {}).items()
                                )[-20:]
                                if len(result["content"]) > INLINE_RESULT_CHARACTERS
                            ],
                        }
                    ),
                )
                del value["plan_messages"]
        elif request.resume is not None:
            pending = {item["id"]: item for item in current["pending"]}
            if set(request.resume) != set(pending):
                raise HTTPException(
                    409, "Pending request changed; reload and try again"
                )
            for key, answer in request.resume.items():
                try:
                    if pending[key]["kind"] == "questions":
                        validate_answers(pending[key]["questions"], answer)
                    elif pending[key]["kind"] == "plan_approval":
                        validate_plan_decision(pending[key]["brief_revision"], answer)
                    elif pending[key]["kind"] == "tool_approval":
                        validate_tool_decision(answer)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            value = Command(resume=request.resume, update=updates)
        elif snapshot.next and not current["pending"]:
            value = None
        else:
            return current
    # The build stage can spend a while generating and validating a full
    # @dograh/sdk workflow — especially routed through OpenRouter to a capable
    # model. The SSE route heartbeats every 15s, so a longer deadline is safe.
    async with asyncio.timeout(300):
        await graph.ainvoke(value, config={**config, "recursion_limit": 40})
    return present(await graph.aget_state(config), session_id)


async def builder_step(
    session_id: UUID, organization_id: int, user_id: str, request: BuilderStep | None
):
    thread_id = f"workflow-builder:{organization_id}:{user_id}:{session_id}"
    model = BuilderChatModel(
        organization_id=organization_id,
        user_id=user_id,
    )
    async with db_client.builder_checkpointer(
        thread_id, **({"readonly": True} if request is None else {})
    ) as saver:
        config = {"configurable": {"thread_id": thread_id}}
        saved = await saver.aget_tuple(config)
        tools = (request.tools if request else []) or (
            saved.checkpoint["channel_values"].get("tools", []) if saved else []
        )
        graph = build_agent(model, saver, tools)
        return await run_step(graph, config, session_id, request)
