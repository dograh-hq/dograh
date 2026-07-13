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
    deploy_id: str
    org_id: str
    room_name: str
    channel: str = "voice_sip"
    agent_id: str = ""
    llm_model: str = "unknown"


class HangupRequest(BaseModel):
    session_id: str
    org_id: str
    deploy_id: str
    room_name: str = ""
    duration_sec: float = 0
    outcome: str = "completed"
    channel: str = "voice_sip"


# ── Runtime Config ──────────────────────────────────────────────────────────


@router.get("/deploy/{deploy_id}/runtime-config")
async def get_runtime_config(
    deploy_id: str,
    _token: None = Depends(_verify_internal_token),
):
    """Return full runtime config for a deploy — consumed by dograh-livekit."""
    from api.db import db_client

    deploy = await db_client.get_deploy(deploy_id)
    if not deploy:
        raise HTTPException(status_code=404, detail="Deploy not found")

    workflow = await db_client.get_workflow(deploy.workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Resolve tools from UUIDs
    tools = []
    for node in workflow.definition.get("nodes", []):
        node_data = node.get("data") or {}
        tool_uuids = node_data.get("tool_uuids") or []
        for uuid in tool_uuids:
            try:
                tool_def = await db_client.get_tool_definition(uuid, deploy.organization_id)
                if tool_def:
                    tools.append(tool_def)
            except Exception:
                pass

    # Resolve KB refs
    kb_refs = []
    for node in workflow.definition.get("nodes", []):
        node_data = node.get("data") or {}
        doc_uuids = node_data.get("document_uuids") or []
        kb_refs.extend(doc_uuids)
    kb_refs = list(set(kb_refs))

    return {
        "deploy_id": deploy_id,
        "org_id": str(deploy.organization_id),
        "agent_id": str(deploy.agent_id) if deploy.agent_id else "",
        "agent_name": deploy.name if hasattr(deploy, "name") else "",
        "workflow_graph": workflow.definition,
        "llm_config": getattr(deploy, "llm_config", {}) or {},
        "stt_config": getattr(deploy, "stt_config", {}) or {},
        "tts_config": getattr(deploy, "tts_config", {}) or {},
        "system_prompt": getattr(deploy, "system_prompt", "") or "",
        "greeting_message": getattr(deploy, "greeting_message", "") or "",
        "tools": tools,
        "kb_refs": kb_refs,
        "handoff_sip_number": getattr(deploy, "handoff_sip_number", "") or "",
        "orchestrator_mode": "agentos",
        "stages": workflow.definition.get("nodes", []),
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
        session = await db_client.create_session(
            deploy_id=body.deploy_id,
            org_id=int(body.org_id),
            room_name=body.room_name,
            channel=body.channel,
            agent_id=body.agent_id,
            llm_model=body.llm_model,
        )
    except AttributeError:
        # Fallback: create_session may not exist yet; return a stub
        return {"id": f"session_{body.deploy_id}_{body.room_name}", "status": "active"}

    return {"id": str(session.id) if hasattr(session, "id") else "unknown", "status": "active"}


@router.put("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: dict,
    _token: None = Depends(_verify_internal_token),
):
    """Update a session record."""
    from api.db import db_client

    org_id = body.pop("org_id", None)
    try:
        await db_client.update_session(session_id, **body)
    except Exception:
        pass  # Non-blocking — session updates are best-effort

    return {"status": "updated"}


@router.post("/sessions/hangup")
async def hangup_session(
    body: HangupRequest,
    _token: None = Depends(_verify_internal_token),
):
    """Handle session hangup notification."""
    try:
        from api.db import db_client
        await db_client.update_session(
            body.session_id,
            duration_sec=body.duration_sec,
            outcome=body.outcome,
        )
    except Exception:
        pass  # Non-blocking

    return {"status": "ok"}
