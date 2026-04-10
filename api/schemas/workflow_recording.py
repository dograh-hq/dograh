"""Pydantic schemas for workflow recording operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RecordingUploadResponseSchema(BaseModel):
    """Response schema with presigned upload URL."""

    upload_url: str = Field(..., description="Presigned URL for uploading the audio")
    recording_id: str = Field(..., description="Short unique recording ID")
    storage_key: str = Field(..., description="Storage key where file will be uploaded")


class FileDescriptor(BaseModel):
    """Descriptor for a single file in a batch upload request."""

    filename: str = Field(..., description="Original filename of the audio file")
    mime_type: str = Field(
        default="audio/wav", description="MIME type of the audio file"
    )
    file_size: int = Field(
        ...,
        gt=0,
        le=5_242_880,
        description="File size in bytes (max 5MB)",
    )


class BatchRecordingUploadRequestSchema(BaseModel):
    """Request schema for getting presigned upload URLs for one or more files."""

    workflow_id: int = Field(..., description="Workflow ID these recordings belong to")
    files: List[FileDescriptor] = Field(
        ..., min_length=1, max_length=20, description="List of files to upload"
    )


class BatchRecordingUploadResponseSchema(BaseModel):
    """Response schema with presigned upload URLs."""

    items: List[RecordingUploadResponseSchema] = Field(
        ..., description="Upload URLs for each file"
    )


class RecordingCreateRequestSchema(BaseModel):
    """Request schema for creating a recording record after upload."""

    recording_id: str = Field(..., description="Short recording ID from upload step")
    workflow_id: int = Field(..., description="Workflow ID")
    tts_provider: str = Field(..., description="TTS provider (e.g. elevenlabs)")
    tts_model: str = Field(..., description="TTS model name")
    tts_voice_id: str = Field(..., description="TTS voice identifier")
    transcript: str = Field(
        ..., description="User-provided transcript of the recording"
    )
    storage_key: str = Field(..., description="Storage key from upload step")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional metadata (file_size, duration, etc.)"
    )


class RecordingResponseSchema(BaseModel):
    """Response schema for a single recording."""

    id: int
    recording_id: str
    workflow_id: int
    organization_id: int
    tts_provider: str
    tts_model: str
    tts_voice_id: str
    transcript: str
    storage_key: str
    storage_backend: str
    metadata: Dict[str, Any]
    created_by: int
    created_at: datetime
    is_active: bool


class BatchRecordingCreateRequestSchema(BaseModel):
    """Request schema for creating one or more recording records after upload."""

    recordings: List[RecordingCreateRequestSchema] = Field(
        ..., min_length=1, max_length=20, description="List of recordings to create"
    )


class BatchRecordingCreateResponseSchema(BaseModel):
    """Response schema for recording creation."""

    recordings: List[RecordingResponseSchema] = Field(
        ..., description="Created recording records"
    )


class RecordingUpdateRequestSchema(BaseModel):
    """Request schema for updating a recording's ID."""

    recording_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="New descriptive recording ID",
    )


class RecordingListResponseSchema(BaseModel):
    """Response schema for list of recordings."""

    recordings: List[RecordingResponseSchema]
    total: int
