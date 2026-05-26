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

Each failure path returns an `error_code` via `_error_result`. Those
codes and their meanings are documented in the `save_workflow` docstring
(the description shipped to the LLM via `tools/list`); keep the two in
sync — `test_mcp_instructions_drift.py` enforces it. All LLM-facing
errors include file:line:column where available so the LLM can correct
its code directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError as PydanticValidationError

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tools._workflow_projection import (
    select_workflow_projection_source,
)
from api.mcp_server.tracing import traced_tool
from api.mcp_server.ts_bridge import TsBridgeError, parse_code
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.layout import reconcile_positions
from api.services.workflow.trigger_paths import validate_trigger_paths
from api.services.workflow.workflow_graph import WorkflowGraph


async def _previous_workflow_json(workflow: Any) -> dict[str, Any] | None:
    """Match the agent-facing read tools' source selection."""
    source = await select_workflow_projection_source(workflow)
    return source.payload


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

    On failure the result has `saved: false`, a machine-readable
    `error_code`, and a human-readable `error` (with file:line:column
    where the problem is locatable). Resubmit the full corrected source —
    patches are not accepted. Possible `error_code` values:
    - `parse_error` — disallowed construct or malformed TypeScript.
    - `validation_error` — node data failed spec validation (unknown
      field, missing required, wrong type, option out of range).
    - `schema_validation` — wire-format (DTO) rejection; rare.
    - `graph_validation` — structural rule broken (e.g. no start node,
      unreachable node, edge to/from the wrong node type).
    - `bridge_error` — internal/transient; retry once, then surface it.
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
    trigger_path_issues = validate_trigger_paths(payload)
    if trigger_path_issues:
        return _error_result(
            "validation_error",
            "\n".join(issue.message for issue in trigger_path_issues),
        )

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
