"""Dograh-owned builder orchestration and authenticated in-process MCP tools."""

import asyncio
import json
from contextlib import aclosing
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from fastmcp import Client
from loguru import logger
from pipecat.utils.run_context import set_current_org_id

from api.db import db_client
from api.db.models import UserModel
from api.db.workflow_builder import BuilderBusy
from api.mcp_server.auth import builder_mcp_user
from api.mcp_server.server import mcp
from api.mcp_server.tools._workflow_projection import (
    project_workflow_to_sdk_view,
    select_workflow_projection_source,
)
from api.mcp_server.ts_bridge import TsBridgeError
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
from api.services.workflow.layout import reconcile_positions

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


def _edit_session_id(user, workflow_id: int) -> UUID:
    # Deterministic per-workflow session so repeated edits continue the same
    # conversation. Used when the workflow has no stored creation session.
    return uuid5(
        NAMESPACE_URL,
        f"dograh-builder:{user.selected_organization_id}:{user.id}:edit:{workflow_id}",
    )


async def load_edit_session(user, workflow_id: int) -> BuilderSession:
    """Open the builder on an existing workflow, resuming its conversation."""
    if not user.selected_organization_id:
        raise HTTPException(403, "Select an organization first")
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(404, "Workflow not found")
    # Resume the original creation chat when we stored its id; otherwise use the
    # deterministic per-workflow edit session.
    stored = (workflow.workflow_configurations or {}).get("builder_session_id")
    try:
        session_id = UUID(stored) if stored else _edit_session_id(user, workflow_id)
    except (ValueError, TypeError):
        session_id = _edit_session_id(user, workflow_id)
    try:
        session = await load_session(user, session_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            # No checkpoint yet: a fresh edit session the UI can start into.
            return BuilderSession(id=session_id, workflow_id=workflow_id)
        raise
    session.workflow_id = workflow_id
    return session


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
    # Route builder traces to this org's Langfuse project. `_OrgAttributeSpanProcessor`
    # stamps `dograh.org_id` from this context var at span start and
    # `_OrgRoutingExporter` routes on it. Every other trace-producing path sets this;
    # the builder omitted it, so its spans fell to the env-default exporter (or were
    # dropped). Set before `builder_trace` so the span is stamped on creation.
    set_current_org_id(user.selected_organization_id)
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
            # Edit mode: seed the build stage from the workflow's live code so a
            # refinement starts from the current agent, including manual canvas
            # edits made after it was built. Only a new message starts a build.
            previous_code = None
            if request.workflow_id is not None and request.message is not None:
                workflow = await db_client.get_workflow(
                    request.workflow_id,
                    organization_id=user.selected_organization_id,
                )
                if workflow is None:
                    raise HTTPException(404, "Workflow not found")
                try:
                    previous_code = (await project_workflow_to_sdk_view(workflow))[
                        "code"
                    ]
                except TsBridgeError:
                    logger.exception("Failed to project workflow {} for editing", request.workflow_id)
                    yield {
                        "type": "error",
                        "message": "Could not load the current workflow to edit. Try again.",
                    }
                    return
            body = {
                "request_id": str(request.request_id),
                "checkpoint": request.checkpoint,
                "message": request.message,
                "resume": resume,
                "tools": specs,
                "workflow_id": request.workflow_id,
                "previous_code": previous_code,
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
    set_current_org_id(user.selected_organization_id)
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
    if state.workflow_id is not None:
        return await _save_edit_draft(user, state.workflow_id, result)
    # One workflow per conversation. Existing UUID uniqueness makes retries atomic.
    # Store the session id so the workflow can later reopen this conversation.
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
            workflow_configurations={"builder_session_id": str(session_id)},
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return workflow.id


async def _save_edit_draft(user, workflow_id: int, result: dict) -> int:
    """Save a reviewed edit as a new draft version of an existing workflow."""
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if workflow is None:
        raise HTTPException(404, "Workflow not found")
    # The parser drops node positions; fill them back from the working copy so
    # the canvas keeps its layout, placing only genuinely new nodes.
    source = await select_workflow_projection_source(workflow)
    payload = reconcile_positions(result["workflow"], source.payload)
    if result["name"] and result["name"] != workflow.name:
        await db_client.update_workflow(
            workflow_id=workflow_id,
            name=result["name"],
            workflow_definition=None,
            template_context_variables=None,
            workflow_configurations=None,
            organization_id=user.selected_organization_id,
        )
    await db_client.save_workflow_draft(
        workflow_id=workflow_id, workflow_definition=payload
    )
    return workflow_id


def encode_event(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"
