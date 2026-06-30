"""Public download endpoints for workflow recordings and transcripts.

These endpoints provide secure, token-based public access to workflow artifacts
without requiring authentication. Tokens are generated on-demand during
post-call processing for runs that execute integrations, QA, or campaign
reporting.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from loguru import logger

from api.db import db_client
from api.services.storage import get_storage_for_backend
from api.utils.recording_artifacts import (
    get_recording_storage_backend,
    get_recording_storage_key,
)

router = APIRouter(prefix="/public/download")


@router.get("/workflow/{token}/{artifact_type}")
async def download_workflow_artifact(
    token: str,
    artifact_type: str,
    inline: bool = Query(
        default=False, description="Display inline in browser instead of download"
    ),
):
    """Download a workflow recording or transcript via public access token.

    This endpoint:
    1. Validates the public access token
    2. Looks up the corresponding workflow run
    3. Generates a signed URL for the requested artifact
    4. Redirects to the signed URL

    Args:
        token: The public access token (UUID format)
        artifact_type: Type of artifact - "recording", "transcript",
            "user_recording", or "bot_recording"
        inline: If true, sets Content-Disposition to inline for browser preview

    Returns:
        RedirectResponse to the signed URL (302 redirect)

    Raises:
        HTTPException 400: If artifact type is unsupported
        HTTPException 404: If token is invalid or artifact not found
    """
    # 1. Lookup workflow run by token
    workflow_run = await db_client.get_workflow_run_by_public_token(token)
    if not workflow_run:
        logger.warning(f"Invalid public access token: {token[:8]}...")
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    # 2. Get file path based on artifact type
    artifact_storage_backend = None
    if artifact_type == "recording":
        file_path = workflow_run.recording_url
    elif artifact_type == "transcript":
        file_path = workflow_run.transcript_url
    elif artifact_type == "user_recording":
        file_path = get_recording_storage_key(workflow_run.extra, "user")
        artifact_storage_backend = get_recording_storage_backend(
            workflow_run.extra, "user"
        )
    elif artifact_type == "bot_recording":
        file_path = get_recording_storage_key(workflow_run.extra, "bot")
        artifact_storage_backend = get_recording_storage_backend(
            workflow_run.extra, "bot"
        )
    else:
        logger.warning(
            f"Unsupported artifact type: type={artifact_type}, workflow_run_id={workflow_run.id}"
        )
        raise HTTPException(status_code=400, detail="Unsupported artifact type")

    if not file_path:
        logger.warning(
            f"Artifact not found: type={artifact_type}, workflow_run_id={workflow_run.id}"
        )
        raise HTTPException(
            status_code=404,
            detail=f"No {artifact_type} available for this workflow run",
        )

    # 3. Get storage backend for this workflow run
    try:
        storage = get_storage_for_backend(
            artifact_storage_backend or workflow_run.storage_backend
        )
    except ValueError as e:
        logger.error(f"Invalid storage backend: {workflow_run.storage_backend}")
        raise HTTPException(status_code=500, detail="Storage configuration error")

    # 4. Generate signed URL (1 hour expiration)
    try:
        signed_url = await storage.aget_signed_url(
            file_path=file_path,
            expiration=3600,  # 1 hour
            force_inline=inline,
        )
    except Exception as e:
        logger.error(f"Failed to generate signed URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    if not signed_url:
        logger.error(f"Storage returned None for signed URL: {file_path}")
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    logger.info(
        f"Generated signed URL for {artifact_type}: workflow_run_id={workflow_run.id}, token={token[:8]}..."
    )

    # 5. Redirect to signed URL
    return RedirectResponse(url=signed_url, status_code=302)
