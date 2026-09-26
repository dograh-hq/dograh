"""Authenticated call replay endpoints for CALMOS Connect."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.services.auth.depends import get_user
from api.services.storage import get_storage_for_backend, storage_fs

router = APIRouter(prefix="/call-history", tags=["call-history"])


class CallReplayResponse(BaseModel):
    call_id: str
    agent_run_id: int
    recording_signed_url: str | None = None
    expires_in: int = Field(ge=60, le=900)
    transcript: str | None = None
    utterances: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/{call_id}/replay", response_model=CallReplayResponse)
async def get_call_replay(
    call_id: str,
    expires_in: int = Query(default=300, ge=60, le=900),
    user=Depends(get_user),
) -> CallReplayResponse:
    record = await db_client.get_call_replay_for_user(
        call_id,
        organization_id=user.selected_organization_id,
        is_superuser=user.is_superuser,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Call not found")

    signed_url = None
    if record.get("recording_key"):
        backend = record.get("storage_backend")
        storage = get_storage_for_backend(backend) if backend else storage_fs
        signed_url = await storage.aget_signed_url(
            record["recording_key"], expiration=expires_in, force_inline=True
        )
        if not signed_url:
            raise HTTPException(status_code=503, detail="Recording is temporarily unavailable")

    return CallReplayResponse(
        call_id=record["call_id"],
        agent_run_id=record["agent_run_id"],
        recording_signed_url=signed_url,
        expires_in=expires_in,
        transcript=record.get("transcript"),
        utterances=record.get("utterances", []),
    )
