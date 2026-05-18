"""MCP tool that accepts LLM-authored SDK TypeScript and saves it as a draft.

Execution flow:
    1. Parse via the Node TS validator — AST-only, never executes the code.
       Returns either a workflow JSON or per-location parse/validate errors.
    2. Pydantic validation via `ReactFlowDTO.model_validate` (defence in
       depth; the parser is already spec-driven, but the DTO layer is the
       authoritative wire-format gate).
    3. Graph validation via `WorkflowGraph`.
    4. Save as a new draft via `db_client.save_workflow_draft` — the
       published version stays intact, so edits are rollback-safe.

Error codes surfaced to the LLM:
    parse_error       — TS parse failed or a disallowed construct was used
    validation_error  — node data failed spec validation (unknown field,
                        missing required, wrong type, option out of range)
    schema_validation — ReactFlowDTO Pydantic rejection (rare; parser bug)
    graph_validation  — semantic graph rule broken (e.g. no start node)
    bridge_error      — Node subprocess failed before returning JSON

All LLM-facing errors include file:line:column where available so the
LLM can correct its code directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError as PydanticValidationError

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.mcp_server.ts_bridge import TsBridgeError, parse_code
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.layout import reconcile_positions
from api.services.workflow.workflow_graph import WorkflowGraph


async def _previous_workflow_json(workflow: Any) -> dict[str, Any] | None:
    """Same selection priority as `get_workflow_code` — the version the
    LLM saw is the version we reconcile against.

    `current_definition` (is_current=True) is the published row, so the
    draft must be fetched explicitly. If no draft exists (e.g. the last
    draft was just published), fall through to `released_definition`.
    """
    draft = await db_client.get_draft_version(workflow.id)
    if draft is not None and draft.workflow_json:
        return draft.workflow_json
    released = workflow.released_definition
    if released is not None and released.workflow_json:
        return released.workflow_json
    return workflow.workflow_definition or None


def _error_result(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"saved": False, "error_code": code, "error": message, **extra}


def _format_errors(errors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for e in errors:
        loc = ""
        line = e.get("line")
        col = e.get("column")
        if line is not None:
            loc = f" (line {line}" + (f", col {col}" if col is not None else "") + ")"
        parts.append(f"{e.get('message', '')}{loc}")
    return "\n".join(parts)


@traced_tool
async def save_workflow(workflow_id: int, code: str) -> dict[str, Any]:
    """Parse SDK TypeScript and save the resulting workflow as a draft.

    `code` is TypeScript source using `@dograh/sdk`. Fetch the current
    code first via `get_workflow_code(workflow_id)`, edit it, then pass
    the full updated source here.

    Example code:
        import { Workflow } from "@dograh/sdk";
        import { startCall, endCall } from "@dograh/sdk/typed";

        const wf = new Workflow({ name: "lead_qualification" });
        const greeting = wf.addTyped(startCall({ name: "Greeting", prompt: "Hi!" }));
        const done     = wf.addTyped(endCall({ name: "Done", prompt: "Bye." }));
        wf.edge(greeting, done, { label: "done", condition: "conversation complete" });

    On success the draft version is saved; the published version is
    untouched.
    """
    user = await authenticate_mcp_request()

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    # 1. Parse + spec-validate via the Node TS validator.
    try:
        parsed = await parse_code(code)
    except TsBridgeError as e:
        logger.warning(f"ts_bridge failure: {e}")
        return _error_result("bridge_error", str(e))

    if not parsed.get("ok"):
        stage = parsed.get("stage", "parse")
        errs = parsed.get("errors") or []
        code_key = "parse_error" if stage == "parse" else "validation_error"
        return _error_result(code_key, _format_errors(errs), errors=errs)

    payload = parsed["workflow"]
    new_name = (parsed.get("workflowName") or "").strip()

    # 1b. Reconcile node positions against the previously-stored workflow.
    # The parser drops positions by design (LLMs don't place nodes well);
    # here we fill them back in from what was there before, and pick
    # approximate placements for newly-introduced nodes.
    payload = reconcile_positions(payload, await _previous_workflow_json(workflow))

    # 2. Pydantic shape check (defence in depth — parser is spec-driven).
    try:
        dto = ReactFlowDTO.model_validate(payload)
    except PydanticValidationError as e:
        return _error_result("schema_validation", str(e))

    # 3. Graph-level semantic validation (start-node count, edge shape).
    try:
        WorkflowGraph(dto)
    except (ValueError, Exception) as e:  # WorkflowGraph raises ValueError
        return _error_result("graph_validation", str(e))

    # 4a. If the `new Workflow({ name })` in the edited source differs from
    # the stored name, rename the workflow. Name is a workflow-level field
    # (not versioned), so this takes effect immediately.
    name_changed = bool(new_name) and new_name != workflow.name
    if name_changed:
        await db_client.update_workflow(
            workflow_id=workflow_id,
            name=new_name,
            workflow_definition=None,
            template_context_variables=None,
            workflow_configurations=None,
            organization_id=user.selected_organization_id,
        )

    # 4b. Save as a new draft (existing published version stays intact).
    draft = await db_client.save_workflow_draft(
        workflow_id=workflow_id,
        workflow_definition=payload,
    )

    return {
        "saved": True,
        "workflow_id": workflow_id,
        "version_number": draft.version_number,
        "status": draft.status,
        "node_count": len(payload["nodes"]),
        "edge_count": len(payload["edges"]),
        "name": new_name or workflow.name,
        "renamed": name_changed,
    }
