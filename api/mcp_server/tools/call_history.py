"""MCP tools for sampling call history and fetching transcripts.

Used by the review-calls skill in the agent-loop plugin to pull real
call data without direct database or storage access from the LLM.
"""

import asyncio
import os
import tempfile

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.storage import get_storage_for_backend


@traced_tool
async def list_calls(
    workflow_id: int,
    limit: int = 10,
    min_duration_seconds: float = 30.0,
    disposition: str | None = None,
) -> list[dict]:
    """List completed voice calls for a workflow, newest first.

    Returns id, call_type, duration_seconds, disposition, and created_at
    for each call. The id is the call identifier — pass it straight to
    get_call_transcript, and it doubles as the Axiom `extra.run_id`
    correlator. Excludes text-chat sessions and incomplete runs.

    Args:
        workflow_id: The workflow (agent) ID to query.
        limit: Maximum number of calls to return (default 10, max 100).
        min_duration_seconds: Exclude calls shorter than this (default 30s).
            Pass 0 to include all durations.
        disposition: Filter to a specific mapped_call_disposition value,
            e.g. "connected" or "no_answer". Omit to return all dispositions.
    """
    user = await authenticate_mcp_request()

    limit = min(limit, 100)

    runs = await db_client.list_voice_runs_for_workflow(
        workflow_id=workflow_id,
        organization_id=user.selected_organization_id,
        limit=limit,
        min_duration_seconds=min_duration_seconds,
        disposition_filter=disposition,
    )

    return [
        {
            "id": r["id"],
            "call_type": r["call_type"],
            "duration_seconds": r["duration_seconds"],
            "disposition": r["disposition"],
            "created_at": r["created_at"],
        }
        for r in runs
    ]


@traced_tool
async def get_call_transcript(call_id: int) -> str:
    """Fetch the speaker-labeled transcript for a single call.

    Returns the transcript as a plain-text string. Returns an empty string
    only when no transcript was ever recorded for this call (no stored
    transcript file). A storage/download failure raises an error instead,
    so callers can tell "no transcript" apart from "couldn't fetch it".

    Args:
        call_id: The call id from list_calls (the run's integer PK).
    """
    user = await authenticate_mcp_request()

    run = await db_client.get_workflow_run(
        run_id=call_id,
        organization_id=user.selected_organization_id,
    )
    if not run:
        raise HTTPException(
            status_code=404, detail=f"Call {call_id} not found"
        )

    if not run.transcript_url:
        return ""

    try:
        storage = get_storage_for_backend(run.storage_backend)
    except ValueError:
        logger.error(
            f"get_call_transcript: unknown storage backend "
            f"'{run.storage_backend}' for call {call_id}"
        )
        raise HTTPException(status_code=500, detail="Storage configuration error")

    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)

        ok = await storage.adownload_file(run.transcript_url, tmp_path)
        if not ok:
            logger.warning(
                f"get_call_transcript: download failed for call {call_id}, "
                f"key={run.transcript_url}"
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Transcript exists for call {call_id} but could not be "
                    f"downloaded from storage"
                ),
            )

        content = await asyncio.to_thread(_read_text, tmp_path)
        return content
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"get_call_transcript: error reading call {call_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch transcript")
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
