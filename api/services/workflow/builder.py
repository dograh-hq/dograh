"""Dograh-owned builder orchestration and authenticated in-process MCP tools."""

import asyncio
import json
from contextlib import aclosing
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from fastmcp import Client
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.db.workflow_builder import BuilderBusy
from api.mcp_server.auth import builder_mcp_user
from api.mcp_server.server import mcp
from api.schemas.workflow_builder import BuilderSession, BuilderTurnRequest
from api.services.observability.trace_payloads import trace_json
from api.services.workflow.builder_runtime.agent import ALLOWED_TOOLS as BUILDER_TOOLS
from api.services.workflow.builder_runtime.schemas import BuilderStep
from api.services.workflow.builder_runtime.service import builder_step
from api.services.workflow.builder_runtime.tracing import (
    builder_trace,
    record_turn_event,
)
from api.services.workflow.builder_validation import preview_workflow_code

TOOL_PROGRESS = {
    "preview_workflow": "Validating your workflow…",
    "list_node_types": "Looking at available conversation steps…",
    "get_node_type": "Checking how this conversation step works…",
    "list_tools": "Looking at your integrations…",
    "list_documents": "Looking at your knowledge base…",
    "list_credentials": "Checking available connections…",
    "list_recordings": "Looking at your recordings…",
    "get_voice_prompting_guide": "Reviewing voice conversation guidance…",
}


async def local_step(
    user: UserModel, session_id: UUID, body: dict | None = None
) -> dict:
    if not user.selected_organization_id:
        raise HTTPException(403, "Select an organization first")
    try:
        return await builder_step(
            session_id,
            user.selected_organization_id,
            str(user.id),
            BuilderStep.model_validate(body) if body is not None else None,
        )
    except HTTPException:
        raise
    except BuilderBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Builder timed out. Resume to retry.") from exc
    except Exception:
        logger.exception("Workflow builder step failed")
        raise HTTPException(
            503,
            "Builder is unavailable. Check Dograh's model and checkpoint configuration.",
        )


async def load_session(user, session_id) -> BuilderSession:
    return BuilderSession.model_validate(await local_step(user, session_id))


async def call_builder_tool(client, name: str, arguments: dict) -> dict:
    if name not in BUILDER_TOOLS:
        return {"error": "This tool is not available to the builder"}
    try:
        result = await client.call_tool(name, arguments)
        if result.is_error:
            return {
                "error": "Dograh could not complete this tool request. Check its arguments."
            }
        if result.data is not None:
            return result.data
        return {
            "result": [block.text for block in result.content if hasattr(block, "text")]
        }
    except Exception:
        logger.exception("Builder MCP tool failed: {}", name)
        return {"error": "Dograh tool failed. Check the arguments or try again."}


async def builder_turn(user, session_id: UUID, request: BuilderTurnRequest):
    with builder_trace(
        user=user,
        session_id=session_id,
        operation="turn",
        input=request.model_dump(mode="json"),
        request_id=request.request_id,
    ) as span:
        async with aclosing(_builder_turn(user, session_id, request)) as events:
            async for event in events:
                record_turn_event(span, event)
                yield event


async def _builder_turn(user, session_id: UUID, request: BuilderTurnRequest):
    """Relay tools automatically; pause for questions and human plan decisions."""
    token = builder_mcp_user.set(user)
    try:
        async with Client(mcp) as client:
            specs = [
                item.model_dump(mode="json", by_alias=True)
                for item in await client.list_tools()
                if item.name in BUILDER_TOOLS
            ]
            if request.answers is not None:
                previous = await local_step(user, session_id)
                pending = {item["id"]: item for item in previous["pending"]}
                # Browser input may answer questions, never impersonate MCP results.
                if not request.answers or any(
                    key not in pending or pending[key]["kind"] != "questions"
                    for key in request.answers
                ):
                    raise HTTPException(422, "Only pending questions can be answered")
                if set(request.answers) != {
                    key
                    for key, value in pending.items()
                    if value["kind"] == "questions"
                }:
                    raise HTTPException(422, "Answer all pending questions")
                resume = dict(request.answers)
                for key, item in pending.items():
                    if item["kind"] == "mcp":
                        resume[key] = await call_builder_tool(
                            client, item["tool_name"], item["arguments"]
                        )
            elif request.approval is not None:
                previous = await local_step(user, session_id)
                pending = {item["id"]: item for item in previous["pending"]}
                approval = request.approval
                if (
                    set(pending) != {approval.interrupt_id}
                    or pending[approval.interrupt_id]["kind"] != "plan_approval"
                ):
                    raise HTTPException(
                        409, "No matching plan to review; reload and try again"
                    )
                resume = {
                    approval.interrupt_id: approval.model_dump(exclude={"interrupt_id"})
                }
            else:
                resume = None
            body = {
                "request_id": str(request.request_id),
                "checkpoint": request.checkpoint,
                "message": request.message,
                "resume": resume,
                "tools": specs,
            }
            yield {"type": "status", "message": "Thinking about your agent…"}
            for _ in range(30):
                state = await local_step(user, session_id, body)
                yield {
                    "type": "session",
                    "session": BuilderSession.model_validate(state).model_dump(
                        mode="json"
                    ),
                }
                pending = state["pending"]
                if not pending or any(item["kind"] != "mcp" for item in pending):
                    yield {"type": "done"}
                    return
                results = {}
                for item in pending:
                    name = item["tool_name"]
                    yield {
                        "type": "status",
                        "message": TOOL_PROGRESS.get(name, "Checking your agent…"),
                    }
                    results[item["id"]] = await call_builder_tool(
                        client, name, item["arguments"]
                    )
                body = {
                    "request_id": str(uuid5(request.request_id, state["checkpoint"])),
                    "checkpoint": state["checkpoint"],
                    "resume": results,
                    "tools": specs,
                }
            yield {
                "type": "error",
                "message": "The builder reached its step limit. Resume to continue.",
            }
    except HTTPException as exc:
        yield {"type": "error", "message": str(exc.detail)}
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Builder conversation failed")
        yield {
            "type": "error",
            "message": "The builder stopped unexpectedly. Resume to continue.",
        }
    finally:
        builder_mcp_user.reset(token)


async def save_builder_draft(user, session_id: UUID, checkpoint: str) -> int:
    with builder_trace(
        user=user,
        session_id=session_id,
        operation="save",
        input={"checkpoint": checkpoint},
    ) as span:
        workflow_id = await _save_builder_draft(user, session_id, checkpoint)
        span.set_attribute("builder.outcome", "saved")
        span.set_attribute(
            "langfuse.observation.output", trace_json({"workflow_id": workflow_id})
        )
        return workflow_id


async def _save_builder_draft(user, session_id: UUID, checkpoint: str) -> int:
    state = await load_session(user, session_id)
    if state.checkpoint != checkpoint or state.can_continue or not state.proposal:
        raise HTTPException(
            409, "Finish building and review the latest proposal before saving"
        )
    result = await preview_workflow_code(
        state.proposal.code, user.selected_organization_id
    )
    if not result["valid"]:
        raise HTTPException(
            422,
            result.get(
                "error", "Workflow validation failed; ask the builder to fix it"
            ),
        )
    # One workflow per conversation. Existing UUID uniqueness makes retries atomic.
    try:
        workflow = await db_client.create_builder_draft(
            workflow_uuid=str(
                uuid5(
                    NAMESPACE_URL,
                    f"dograh-builder:{user.selected_organization_id}:{user.id}:{session_id}",
                )
            ),
            name=result["name"],
            workflow_definition=result["workflow"],
            user_id=user.id,
            organization_id=user.selected_organization_id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return workflow.id


def encode_event(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
