"""Internal API endpoints for dograh-livekit bridge.

All endpoints are protected by X-Internal-Token header.
Not versioned — these are private, system-internal endpoints.
"""

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/internal",
    tags=["internal"],
    responses={404: {"description": "Not found"}},
)


# ── Auth ────────────────────────────────────────────────────────────────────


async def _verify_internal_token(x_internal_token: str = Header(...)):
    expected = os.getenv("DOGRAH_INTERNAL_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if x_internal_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Schemas ─────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    kb_refs: list[str] | None = None


class CreateSessionRequest(BaseModel):
    workflow_id: str
    org_id: str
    room_name: str
    channel: str = "voice_sip"
    agent_id: str = ""
    llm_model: str = "unknown"


class HangupRequest(BaseModel):
    session_id: str
    org_id: str
    workflow_id: str
    room_name: str = ""
    duration_sec: float = 0
    outcome: str = "completed"
    channel: str = "voice_sip"


# ── Runtime Config ──────────────────────────────────────────────────────────


@router.get("/workflows/{workflow_id}/runtime-config")
async def get_runtime_config(
    workflow_id: int,
    _token: None = Depends(_verify_internal_token),
):
    """Return full runtime config for a workflow — consumed by dograh-livekit."""
    from api.db import db_client

    workflow = await db_client.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get published definition
    published = await db_client.get_published_workflow_definition(workflow_id)
    if not published:
        raise HTTPException(status_code=404, detail="No published version")

    definition = published.workflow_json or {}
    configs = published.workflow_configurations or {}

    # Resolve tools from UUIDs
    tools = []
    for node in definition.get("nodes", []):
        node_data = node.get("data") or {}
        tool_uuids = node_data.get("tool_uuids") or []
        for uuid in tool_uuids:
            try:
                tool_def = await db_client.get_tool_definition(uuid, workflow.organization_id)
                if tool_def:
                    tools.append(tool_def)
            except Exception:
                pass

    # Resolve KB refs
    kb_refs = []
    for node in definition.get("nodes", []):
        node_data = node.get("data") or {}
        doc_uuids = node_data.get("document_uuids") or []
        kb_refs.extend(doc_uuids)
    kb_refs = list(set(kb_refs))

    # Extract system prompt from global node
    system_prompt = ""
    greeting_message = ""
    for node in definition.get("nodes", []):
        if node.get("type") == "globalNode":
            system_prompt = (node.get("data") or {}).get("prompt", "")
        if node.get("type") == "startCall":
            greeting_message = (node.get("data") or {}).get("greeting", "")

    return {
        "workflow_id": workflow_id,
        "org_id": str(workflow.organization_id),
        "agent_id": str(workflow.id),
        "agent_name": workflow.name,
        "workflow_graph": definition,
        "llm_config": configs.get("llm_config", {}),
        "stt_config": configs.get("stt_config", {}),
        "tts_config": configs.get("tts_config", {}),
        "system_prompt": system_prompt or configs.get("system_prompt", ""),
        "greeting_message": greeting_message,
        "tools": tools,
        "kb_refs": kb_refs,
        "handoff_sip_number": "",
        "orchestrator_mode": "agentos",
        "stages": definition.get("nodes", []),
    }


# ── Knowledge Base ──────────────────────────────────────────────────────────


@router.post("/kb/{org_id}/search")
async def search_knowledge(
    org_id: str,
    body: SearchRequest,
    _token: None = Depends(_verify_internal_token),
):
    """Search the knowledge base for an organization."""
    from api.services.knowledge import search_documents

    results = await search_documents(
        int(org_id),
        body.query,
        document_ids=body.kb_refs or [],
    )
    return {"results": results}


# ── Session Lifecycle ───────────────────────────────────────────────────────


@router.post("/sessions", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    _token: None = Depends(_verify_internal_token),
):
    """Create a session record."""
    from api.db import db_client

    try:
        session = await db_client.create_workflow_run(
            name=f"LK-{body.room_name}",
            workflow_id=int(body.workflow_id),
            mode="livekit_sip",
            user_id=None,
            organization_id=int(body.org_id),
            initial_context={
                "channel": body.channel,
                "room_name": body.room_name,
            },
        )
    except Exception:
        return {"id": f"session_{body.workflow_id}_{body.room_name}", "status": "active"}

    return {"id": str(session.id) if hasattr(session, "id") else "unknown", "status": "active"}


@router.put("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: dict,
    _token: None = Depends(_verify_internal_token),
):
    """Update a session record."""
    try:
        from api.db import db_client
        await db_client.update_workflow_run(
            run_id=int(session_id),
            gathered_context=body.get("context", {}),
            is_completed=body.get("is_completed", False),
        )
    except Exception:
        pass

    return {"status": "updated"}


@router.post("/sessions/hangup")
async def hangup_session(
    body: HangupRequest,
    _token: None = Depends(_verify_internal_token),
):
    """Handle session hangup notification."""
    try:
        from api.db import db_client
        await db_client.update_workflow_run(
            run_id=int(body.session_id) if body.session_id.isdigit() else None,
            is_completed=True,
        )
    except Exception:
        pass

    return {"status": "ok"}
