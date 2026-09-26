"""Authenticated call replay endpoints for CALMOS Connect."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.db import db_client
from api.services.auth.depends import get_user
from api.services.storage import get_storage_for_backend, storage_fs

router = APIRouter(prefix="/call-history", tags=["call-history"])


class RecordingReplayTrack(BaseModel):
    track: str
    signed_url: str


class CallReplayResponse(BaseModel):
    call_id: str
    agent_run_id: int
    recording_signed_url: str | None = None
    expires_in: int = Field(ge=60, le=900)
    transcript: str | None = None
    utterances: list[dict[str, Any]] = Field(default_factory=list)
    recordings: list[RecordingReplayTrack] = Field(default_factory=list)


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
    signed_tracks: list[RecordingReplayTrack] = []
    recording_rows = record.get("recordings") or []
    if not recording_rows and record.get("recording_key"):
        recording_rows = [
            {
                "track": "mixed",
                "storage_backend": record.get("storage_backend"),
                "recording_key": record["recording_key"],
            }
        ]
    for recording in recording_rows:
        backend = recording.get("storage_backend")
        storage = get_storage_for_backend(backend) if backend else storage_fs
        track_url = await storage.aget_signed_url(
            recording["recording_key"], expiration=expires_in, force_inline=True
        )
        if not track_url:
            raise HTTPException(
                status_code=503, detail="Recording is temporarily unavailable"
            )
        track = (
            "assistant" if recording.get("track") == "bot" else recording.get("track")
        )
        signed_tracks.append(RecordingReplayTrack(track=track, signed_url=track_url))
        if recording.get("track") == "mixed":
            signed_url = track_url

    try:
        await db_client.record_audit_event(
            organization_id=record.get("organization_id")
            or user.selected_organization_id,
            workflow_run_id=record["agent_run_id"],
            service_user_id=record.get("service_user_id"),
            actor_user_id=getattr(user, "id", None),
            event_type="recording_replay_url_issued",
            resource_type="workflow_run",
            resource_id=record["call_id"],
            outcome="success",
            event_metadata={
                "expires_in": expires_in,
                "tracks": [item.track for item in signed_tracks],
            },
        )
    except Exception as exc:
        # Replay without an audit record would violate the access-control
        # contract. Do not include object keys or transcript data in the error.
        raise HTTPException(
            status_code=503, detail="Replay auditing is temporarily unavailable"
        ) from exc

    return CallReplayResponse(
        call_id=record["call_id"],
        agent_run_id=record["agent_run_id"],
        recording_signed_url=signed_url,
        expires_in=expires_in,
        transcript=record.get("transcript"),
        utterances=record.get("utterances", []),
        recordings=signed_tracks,
    )
