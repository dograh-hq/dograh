"""Validate builder proposals without executing code or persisting a workflow."""

from typing import Any

from pydantic import ValidationError

from api.db import db_client
from api.mcp_server.ts_bridge import TsBridgeError, parse_code
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.layout import reconcile_positions
from api.services.workflow.tool_name_validation import (
    validate_workflow_tool_name_collisions,
)
from api.services.workflow.trigger_paths import validate_trigger_paths
from api.services.workflow.workflow_graph import WorkflowGraph


async def preview_workflow_code(code: str, organization_id: int) -> dict[str, Any]:
    if len(code) > 100_000:
        return {"valid": False, "error": "Workflow code exceeds 100,000 characters."}
    try:
        parsed = await parse_code(code)
    except TsBridgeError:
        return {
            "valid": False,
            "error": "Workflow validator is unavailable. Try again.",
        }
    if not parsed.get("ok"):
        return {"valid": False, "errors": parsed.get("errors", [])}
    name = (parsed.get("workflowName") or "").strip()
    if not name or len(name) > 200:
        return {"valid": False, "error": "Provide a workflow name of 1–200 characters."}
    payload = reconcile_positions(parsed["workflow"], None)
    try:
        dto = ReactFlowDTO.model_validate(payload)
        WorkflowGraph(dto)
    except (ValueError, ValidationError) as exc:
        return {"valid": False, "error": str(exc)}
    issues = validate_trigger_paths(payload)
    if issues:
        return {"valid": False, "error": "\n".join(issue.message for issue in issues)}
    errors = await validate_workflow_tool_name_collisions(payload, organization_id)
    if errors:
        return {"valid": False, "errors": errors}

    # Resolve every external reference against this organization before preview/save.
    references: dict[str, set[str]] = {
        key: set() for key in ("tool", "document", "credential", "recording")
    }

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                kind = next(
                    (
                        kind
                        for kind in references
                        if key.endswith(f"{kind}_uuid")
                        or key == f"{kind}_uuids"
                        or (kind == "recording" and key.endswith("recording_id"))
                    ),
                    None,
                )
                if kind and item:
                    references[kind].update(
                        str(v) for v in (item if isinstance(item, list) else [item])
                    )
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    catalogs = {
        "tool": (db_client.get_tools_for_organization, "tool_uuid"),
        "document": (db_client.get_documents_for_organization, "document_uuid"),
        "credential": (db_client.get_credentials_for_organization, "credential_uuid"),
        "recording": (db_client.get_recordings, "recording_id"),
    }
    for kind, values in references.items():
        if values:
            fetch, field = catalogs[kind]
            available = {
                str(getattr(row, field))
                for row in await fetch(organization_id=organization_id)
            }
            if not values <= available:
                return {
                    "valid": False,
                    "error": f"Unknown or unavailable {kind} reference. Use the organization's catalog.",
                }
    return {"valid": True, "name": name, "code": code, "workflow": payload}
