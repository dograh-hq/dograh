"""MCP discovery tools for the reference catalogs.

Node properties of type `tool_refs`, `document_refs`, `recording_ref`, and
`credential_ref` carry UUIDs that resolve against these catalogs. LLMs must
list the catalog before populating those fields with real UUIDs.
"""

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.server import mcp
from api.mcp_server.tracing import traced_tool


@mcp.tool
@traced_tool
async def list_tools(status: str | None = "active") -> list[dict]:
    """List tools the agent can invoke during a call.

    Returns each tool's `tool_uuid` (use this in node `tool_uuids` properties),
    `name`, `description`, and `category`. Pass `status=None` to include
    archived tools.
    """
    user = await authenticate_mcp_request()
    tools = await db_client.get_tools_for_organization(
        organization_id=user.selected_organization_id,
        status=status,
    )
    return [
        {
            "tool_uuid": t.tool_uuid,
            "name": t.name,
            "description": t.description or "",
            "category": t.category,
        }
        for t in tools
    ]


@mcp.tool
@traced_tool
async def list_documents() -> list[dict]:
    """List knowledge-base documents the agent can reference during a call.

    Returns each document's `document_uuid` (use this in node
    `document_uuids` properties), `filename`, and `processing_status`.
    """
    user = await authenticate_mcp_request()
    documents = await db_client.get_documents_for_organization(
        organization_id=user.selected_organization_id,
    )
    return [
        {
            "document_uuid": d.document_uuid,
            "filename": d.filename,
            "processing_status": d.processing_status,
            "total_chunks": d.total_chunks,
        }
        for d in documents
    ]


@mcp.tool
@traced_tool
async def list_credentials() -> list[dict]:
    """List external credentials available for webhook auth and pre-call fetch.

    Returns each credential's `credential_uuid` (use this in node
    `credential_uuid` / `pre_call_fetch_credential_uuid` properties), `name`,
    `description`, and `credential_type`.
    """
    user = await authenticate_mcp_request()
    credentials = await db_client.get_credentials_for_organization(
        organization_id=user.selected_organization_id,
    )
    return [
        {
            "credential_uuid": c.credential_uuid,
            "name": c.name,
            "description": c.description or "",
            "credential_type": c.credential_type,
        }
        for c in credentials
    ]


@mcp.tool
@traced_tool
async def list_recordings(workflow_id: int | None = None) -> list[dict]:
    """List pre-recorded audio files available for greetings and edge
    transition speech.

    Returns each recording's `recording_id` (use this in
    `greeting_recording_id` / `transition_speech_recording_id` properties),
    `transcript`, and TTS metadata. Pass `workflow_id` to filter to one
    workflow's recordings.
    """
    user = await authenticate_mcp_request()
    recordings = await db_client.get_recordings(
        organization_id=user.selected_organization_id,
        workflow_id=workflow_id,
    )
    return [
        {
            "id": r.id,
            "recording_id": r.recording_id,
            "workflow_id": r.workflow_id,
            "transcript": r.transcript,
            "tts_provider": r.tts_provider,
            "tts_model": r.tts_model,
            "tts_voice_id": r.tts_voice_id,
        }
        for r in recordings
    ]
