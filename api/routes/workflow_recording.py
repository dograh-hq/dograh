"""API routes for workflow recording operations."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from api.db import db_client
from api.db.workflow_recording_client import generate_short_id
from api.enums import StorageBackend
from api.schemas.workflow_recording import (
    RecordingCreateRequestSchema,
    RecordingListResponseSchema,
    RecordingResponseSchema,
    RecordingUploadRequestSchema,
    RecordingUploadResponseSchema,
)
from api.services.auth.depends import get_user
from api.services.storage import storage_fs

router = APIRouter(prefix="/workflow-recordings", tags=["workflow-recordings"])


async def _generate_unique_recording_id() -> str:
    """Generate a globally unique short recording ID."""
    for _ in range(10):
        rid = generate_short_id(8)
        exists = await db_client.check_recording_id_exists(rid)
        if not exists:
            return rid
    raise HTTPException(
        status_code=500, detail="Failed to generate unique recording ID"
    )


def _build_response(rec) -> RecordingResponseSchema:
    return RecordingResponseSchema(
        id=rec.id,
        recording_id=rec.recording_id,
        workflow_id=rec.workflow_id,
        organization_id=rec.organization_id,
        tts_provider=rec.tts_provider,
        tts_model=rec.tts_model,
        tts_voice_id=rec.tts_voice_id,
        transcript=rec.transcript,
        storage_key=rec.storage_key,
        storage_backend=rec.storage_backend,
        metadata=rec.recording_metadata or {},
        created_by=rec.created_by,
        created_at=rec.created_at,
        is_active=rec.is_active,
    )


@router.post(
    "/upload-url",
    response_model=RecordingUploadResponseSchema,
    summary="Get presigned URL for recording upload",
)
async def get_upload_url(
    request: RecordingUploadRequestSchema,
    user=Depends(get_user),
):
    """Generate a presigned PUT URL for uploading an audio recording."""
    try:
        recording_id = await _generate_unique_recording_id()

        storage_key = (
            f"recordings/{user.selected_organization_id}"
            f"/{request.workflow_id}/{recording_id}"
            f"/{request.filename}"
        )

        upload_url = await storage_fs.aget_presigned_put_url(
            file_path=storage_key,
            expiration=1800,  # 30 minutes
            content_type=request.mime_type,
            max_size=5_242_880,  # 5MB max
        )

        if not upload_url:
            raise HTTPException(
                status_code=500, detail="Failed to generate presigned upload URL"
            )

        logger.info(
            f"Generated recording upload URL: {recording_id}, "
            f"workflow {request.workflow_id}, org {user.selected_organization_id}"
        )

        return RecordingUploadResponseSchema(
            upload_url=upload_url,
            recording_id=recording_id,
            storage_key=storage_key,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error generating recording upload URL: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to generate upload URL"
        ) from exc


@router.post(
    "/",
    response_model=RecordingResponseSchema,
    summary="Create recording record after upload",
)
async def create_recording(
    request: RecordingCreateRequestSchema,
    user=Depends(get_user),
):
    """Create a recording record after the audio has been uploaded to storage."""
    try:
        backend = StorageBackend.get_current_backend()

        recording = await db_client.create_recording(
            recording_id=request.recording_id,
            workflow_id=request.workflow_id,
            organization_id=user.selected_organization_id,
            tts_provider=request.tts_provider,
            tts_model=request.tts_model,
            tts_voice_id=request.tts_voice_id,
            transcript=request.transcript,
            storage_key=request.storage_key,
            storage_backend=backend.value,
            created_by=user.id,
            metadata=request.metadata,
        )

        logger.info(
            f"Created recording {request.recording_id} for workflow {request.workflow_id}"
        )

        return _build_response(recording)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating recording: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to create recording"
        ) from exc


@router.get(
    "/",
    response_model=RecordingListResponseSchema,
    summary="List recordings for a workflow",
)
async def list_recordings(
    workflow_id: Annotated[int, Query(description="Workflow ID")],
    tts_provider: Annotated[
        Optional[str], Query(description="Filter by TTS provider")
    ] = None,
    tts_model: Annotated[
        Optional[str], Query(description="Filter by TTS model")
    ] = None,
    tts_voice_id: Annotated[
        Optional[str], Query(description="Filter by TTS voice ID")
    ] = None,
    user=Depends(get_user),
):
    """List recordings for a workflow, optionally filtered by TTS configuration."""
    try:
        recordings = await db_client.get_recordings_for_workflow(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            tts_provider=tts_provider,
            tts_model=tts_model,
            tts_voice_id=tts_voice_id,
        )

        return RecordingListResponseSchema(
            recordings=[_build_response(r) for r in recordings],
            total=len(recordings),
        )

    except Exception as exc:
        logger.error(f"Error listing recordings: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to list recordings"
        ) from exc


@router.delete(
    "/{recording_id}",
    summary="Delete a recording",
)
async def delete_recording(
    recording_id: str,
    user=Depends(get_user),
):
    """Soft delete a recording."""
    try:
        success = await db_client.delete_recording(
            recording_id=recording_id,
            organization_id=user.selected_organization_id,
        )

        if not success:
            raise HTTPException(status_code=404, detail="Recording not found")

        logger.info(
            f"Deleted recording {recording_id}, org {user.selected_organization_id}"
        )

        return {"success": True, "message": "Recording deleted successfully"}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deleting recording: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to delete recording"
        ) from exc
