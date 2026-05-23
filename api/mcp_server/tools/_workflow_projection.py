from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from api.db import db_client
from api.mcp_server.ts_bridge import generate_code


@dataclass(frozen=True)
class WorkflowProjectionSource:
    payload: dict[str, Any] | None
    version: Literal["draft", "published", "legacy"]
    version_number: int | None


async def select_workflow_projection_source(workflow: Any) -> WorkflowProjectionSource:
    """Choose the same working copy across read and save MCP tools.

    Draft wins over published because that's what a human editor would
    be mutating. Legacy `workflow_definition` is the final fallback for
    older rows that predate versioned definitions.
    """
    draft = await db_client.get_draft_version(workflow.id)
    if draft is not None and draft.workflow_json:
        return WorkflowProjectionSource(
            payload=draft.workflow_json,
            version="draft",
            version_number=draft.version_number,
        )

    released = workflow.released_definition
    if released is not None and released.workflow_json:
        return WorkflowProjectionSource(
            payload=released.workflow_json,
            version="published",
            version_number=released.version_number,
        )

    return WorkflowProjectionSource(
        payload=workflow.workflow_definition or None,
        version="legacy",
        version_number=None,
    )


async def project_workflow_to_sdk_view(workflow: Any) -> dict[str, Any]:
    source = await select_workflow_projection_source(workflow)
    code = await generate_code(source.payload or {}, workflow_name=workflow.name or "")
    return {
        "name": workflow.name or "",
        "version": source.version,
        "version_number": source.version_number,
        "code": code,
    }
