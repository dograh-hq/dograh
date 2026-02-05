"""Pydantic schemas for knowledge base operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocumentUploadRequestSchema(BaseModel):
    """Request schema for initiating document upload."""

    filename: str = Field(..., description="Name of the file to upload")
    mime_type: str = Field(..., description="MIME type of the file")
    custom_metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional custom metadata"
    )


class DocumentUploadResponseSchema(BaseModel):
    """Response schema containing upload URL and document metadata."""

    upload_url: str = Field(..., description="Signed URL for uploading the file")
    document_uuid: str = Field(..., description="Unique identifier for the document")
    s3_key: str = Field(..., description="S3 key where file should be uploaded")


class ProcessDocumentRequestSchema(BaseModel):
    """Request schema for triggering document processing."""

    document_uuid: str = Field(..., description="Document UUID to process")
    s3_key: str = Field(..., description="S3 key of the uploaded file")


class DocumentResponseSchema(BaseModel):
    """Response schema for document metadata."""

    id: int
    document_uuid: str
    filename: str
    file_size_bytes: int
    file_hash: str
    mime_type: str
    processing_status: str  # pending, processing, completed, failed
    processing_error: Optional[str] = None
    total_chunks: int
    custom_metadata: Dict[str, Any]
    docling_metadata: Dict[str, Any]
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    organization_id: int
    created_by: int
    is_active: bool


class DocumentListResponseSchema(BaseModel):
    """Response schema for list of documents."""

    documents: List[DocumentResponseSchema]
    total: int
    limit: int
    offset: int


class ChunkSearchRequestSchema(BaseModel):
    """Request schema for searching similar chunks."""

    query: str = Field(..., description="Search query text")
    limit: int = Field(default=5, ge=1, le=50, description="Maximum number of results")
    document_uuids: Optional[List[str]] = Field(
        default=None, description="Filter by specific document UUIDs"
    )
    min_similarity: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class ChunkResponseSchema(BaseModel):
    """Response schema for a document chunk."""

    id: int
    document_id: int
    chunk_text: str
    contextualized_text: Optional[str]
    chunk_index: int
    chunk_metadata: Dict[str, Any]
    filename: str
    document_uuid: str
    similarity: float


class ChunkSearchResponseSchema(BaseModel):
    """Response schema for chunk search results."""

    chunks: List[ChunkResponseSchema]
    query: str
    total_results: int
