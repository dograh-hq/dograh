"""API routes for workflow recording operations."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.workflow_recording_client import generate_short_id
from api.enums import StorageBackend
from api.schemas.workflow_recording import (
    BatchRecordingCreateRequestSchema,
    BatchRecordingCreateResponseSchema,
    BatchRecordingUploadRequestSchema,
    BatchRecordingUploadResponseSchema,
    RecordingListResponseSchema,
    RecordingResponseSchema,
    RecordingUpdateRequestSchema,
    RecordingUploadResponseSchema,
)
from api.services.auth.depends import get_user
from api.services.mps_service_key_client import mps_service_key_client
from api.services.storage import storage_fs

router = APIRouter(prefix="/workflow-recordings", tags=["workflow-recordings"])


async def _generate_unique_recording_id(organization_id: int) -> str:
    """Generate a unique short recording ID within an organization."""
    for _ in range(10):
        rid = generate_short_id(8)
        exists = await db_client.check_recording_id_exists(rid, organization_id)
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
    response_model=BatchRecordingUploadResponseSchema,
    summary="Get presigned URLs for recording uploads",
)
async def get_upload_urls(
    request: BatchRecordingUploadRequestSchema,
    user=Depends(get_user),
):
    """Generate presigned PUT URLs for uploading one or more audio recordings."""
    try:
        items = []
        for fd in request.files:
            recording_id = await _generate_unique_recording_id(
                user.selected_organization_id
            )

            storage_key = (
                f"recordings/{user.selected_organization_id}"
                f"/{recording_id}"
                f"/{fd.filename}"
            )

            upload_url = await storage_fs.aget_presigned_put_url(
                file_path=storage_key,
                expiration=1800,
                content_type=fd.mime_type,
                max_size=5_242_880,
            )

            if not upload_url:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate presigned upload URL for {fd.filename}",
                )

            items.append(
                RecordingUploadResponseSchema(
                    upload_url=upload_url,
                    recording_id=recording_id,
                    storage_key=storage_key,
                )
            )

        logger.info(
            f"Generated {len(items)} recording upload URL(s), "
            f"org {user.selected_organization_id}"
        )

        return BatchRecordingUploadResponseSchema(items=items)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error generating recording upload URLs: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to generate upload URLs"
        ) from exc


@router.post(
    "/",
    response_model=BatchRecordingCreateResponseSchema,
    summary="Create recording records after upload",
)
async def create_recordings(
    request: BatchRecordingCreateRequestSchema,
    user=Depends(get_user),
):
    """Create one or more recording records after audio files have been uploaded."""
    try:
        backend = StorageBackend.get_current_backend()
        results = []

        for rec_req in request.recordings:
            recording = await db_client.create_recording(
                recording_id=rec_req.recording_id,
                organization_id=user.selected_organization_id,
                transcript=rec_req.transcript,
                storage_key=rec_req.storage_key,
                storage_backend=backend.value,
                created_by=user.id,
                tts_provider=rec_req.tts_provider,
                tts_model=rec_req.tts_model,
                tts_voice_id=rec_req.tts_voice_id,
                metadata=rec_req.metadata,
            )
            results.append(_build_response(recording))

        logger.info(
            f"Created {len(results)} recording(s) for org {user.selected_organization_id}"
        )

        return BatchRecordingCreateResponseSchema(recordings=results)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating recordings: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to create recordings"
        ) from exc


@router.get(
    "/",
    response_model=RecordingListResponseSchema,
    summary="List recordings",
)
async def list_recordings(
    workflow_id: Annotated[
        Optional[int], Query(description="Filter by workflow ID")
    ] = None,
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
    """List recordings for the organization, optionally filtered."""
    try:
        recordings = await db_client.get_recordings(
            organization_id=user.selected_organization_id,
            workflow_id=workflow_id,
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


@router.patch(
    "/{id}",
    response_model=RecordingResponseSchema,
    summary="Update a recording's Recording ID",
)
async def update_recording(
    id: int,
    request: RecordingUpdateRequestSchema,
    user=Depends(get_user),
):
    """Update the recording_id (descriptive name) of a recording."""
    try:
        new_id = request.recording_id.strip()
        if not new_id:
            raise HTTPException(status_code=400, detail="Recording ID cannot be empty")

        existing = await db_client.get_recording_by_id(
            id, user.selected_organization_id
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Recording not found")

        if new_id == existing.recording_id:
            return _build_response(existing)

        exists = await db_client.check_recording_id_exists(
            new_id, user.selected_organization_id
        )
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f"Recording ID '{new_id}' is already in use",
            )

        old_id = existing.recording_id

        recording = await db_client.update_recording_id(
            id=id,
            new_recording_id=new_id,
            organization_id=user.selected_organization_id,
        )

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        # Replace old recording ID in all non-legacy workflow definitions
        updated = await db_client.replace_recording_id_in_workflows(
            old_id=old_id,
            new_id=new_id,
            organization_id=user.selected_organization_id,
        )
        if updated:
            logger.info(
                f"Updated {updated} workflow definition(s) with new recording ID "
                f"'{old_id}' -> '{new_id}'"
            )

        return _build_response(recording)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating recording: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to update recording"
        ) from exc


@router.post(
    "/transcribe",
    summary="Transcribe an audio file",
)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form("en"),
    user=Depends(get_user),
):
    """Transcribe an uploaded audio file using MPS STT."""
    try:
        audio_data = await file.read()

        if DEPLOYMENT_MODE == "oss":
            result = await mps_service_key_client.transcribe_audio(
                audio_data=audio_data,
                filename=file.filename or "audio.wav",
                content_type=file.content_type or "audio/wav",
                language=language,
                created_by=str(user.provider_id),
            )
        else:
            result = await mps_service_key_client.transcribe_audio(
                audio_data=audio_data,
                filename=file.filename or "audio.wav",
                content_type=file.content_type or "audio/wav",
                language=language,
                organization_id=user.selected_organization_id,
            )

        return result

    except Exception as exc:
        logger.error(f"Error transcribing audio: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to transcribe audio"
        ) from exc
