"""MCP tool that accepts LLM-authored SDK TypeScript and creates a new workflow.

Companion to `save_workflow`: where `save_workflow` updates an existing
workflow as a new draft, `create_workflow` brings a workflow into being
in one shot. The resulting workflow is published as version 1 — there
is no prior published version to protect, so we skip the draft step.

Execution flow mirrors `save_workflow`:
    1. Parse via the Node TS validator — AST-only, never executes the code.
    2. Pydantic validation via `ReactFlowDTO.model_validate`.
    3. Graph validation via `WorkflowGraph`.
    4. Persist via `db_client.create_workflow` — workflow row + v1
       published definition in a single transaction.

Error codes surfaced to the LLM match `save_workflow`. An additional
`missing_name` error is returned when the source omits
`new Workflow({ name: "..." })` — the name is required and there is no
prior workflow to fall back to.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import ValidationError as PydanticValidationError

from api.db import db_client
from api.db.agent_trigger_client import TriggerPathConflictError
from api.enums import PostHogEvent
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.mcp_server.ts_bridge import TsBridgeError, parse_code
from api.services.posthog_client import capture_event
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.layout import reconcile_positions
from api.services.workflow.workflow_graph import WorkflowGraph


def _error_result(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"created": False, "error_code": code, "error": message, **extra}


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


def _extract_trigger_paths(workflow_definition: dict) -> list[str]:
    """Mirror of `routes.workflow.extract_trigger_paths` — kept local so the
    MCP layer doesn't depend on the route module."""
    if not workflow_definition:
        return []
    paths: list[str] = []
    for node in workflow_definition.get("nodes") or []:
        if node.get("type") == "trigger":
            trigger_path = (node.get("data") or {}).get("trigger_path")
            if trigger_path:
                paths.append(trigger_path)
    return paths


@traced_tool
async def create_workflow(code: str) -> dict[str, Any]:
    """Parse SDK TypeScript and create a new published workflow.

    `code` is TypeScript source using `@dograh/sdk`. The workflow name
    comes from `new Workflow({ name: "..." })` — it is required.

    Example code:
        import { Workflow } from "@dograh/sdk";
        import { startCall, endCall } from "@dograh/sdk/typed";

        const wf = new Workflow({ name: "lead_qualification" });
        const greeting = wf.addTyped(startCall({ name: "Greeting", prompt: "Hi!" }));
        const done     = wf.addTyped(endCall({ name: "Done", prompt: "Bye." }));
        wf.edge(greeting, done, { label: "done", condition: "conversation complete" });

    On success the new workflow is published as version 1. Use
    `save_workflow(workflow_id, code)` for subsequent edits — those go to
    a draft.
    """
    user = await authenticate_mcp_request()

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
    name = (parsed.get("workflowName") or "").strip()
    if not name:
        return _error_result(
            "missing_name",
            'Workflow name is required. Add `new Workflow({ name: "..." })` to the source.',
        )

    # 1b. New workflow — no prior version to reconcile against; layout
    # places new nodes adjacent to their first incoming neighbor.
    payload = reconcile_positions(payload, None)

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

    # 4. Reject upfront if any trigger path collides with another workflow's
    # trigger in this org so we don't leave an orphan workflow record.
    trigger_paths = _extract_trigger_paths(payload)
    if trigger_paths:
        try:
            await db_client.assert_trigger_paths_available(
                trigger_paths=trigger_paths,
            )
        except TriggerPathConflictError as e:
            return _error_result(
                "trigger_path_conflict", str(e), trigger_paths=e.trigger_paths
            )

    # 5. Persist as a new workflow with v1 published.
    workflow = await db_client.create_workflow(
        name,
        payload,
        user.id,
        user.selected_organization_id,
    )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.WORKFLOW_CREATED,
        properties={
            "workflow_id": workflow.id,
            "workflow_name": workflow.name,
            "source": "mcp",
            "organization_id": user.selected_organization_id,
        },
    )

    if trigger_paths:
        await db_client.sync_triggers_for_workflow(
            workflow_id=workflow.id,
            organization_id=user.selected_organization_id,
            trigger_paths=trigger_paths,
        )

    return {
        "created": True,
        "workflow_id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "version_number": 1,
        "node_count": len(payload["nodes"]),
        "edge_count": len(payload["edges"]),
    }
