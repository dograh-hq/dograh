"""Embed token endpoints for workflows."""

from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.constants import BACKEND_API_ENDPOINT, ENVIRONMENT, UI_APP_URL
from api.db import db_client
from api.db.models import EmbedTokenModel, UserModel
from api.services.auth.depends import get_user

router = APIRouter(prefix="/workflow")


def generate_embed_script(token: EmbedTokenModel) -> str:
    """Generate the embed script for a given token."""
    base_url = str(UI_APP_URL).rstrip("/")

    return f"""<!-- Dograh Voice Widget -->
<script>
  (function(d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '{base_url}/embed/dograh-widget.js?token={token.token}&environment={ENVIRONMENT}&apiEndpoint={BACKEND_API_ENDPOINT}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }}(document, 'script', 'dograh-widget'));
</script>"""


class EmbedTokenRequest(BaseModel):
    allowed_domains: Optional[list[str]] = None
    settings: Optional[dict] = None
    usage_limit: Optional[int] = None
    expires_in_days: Optional[int] = 30


class EmbedTokenResponse(BaseModel):
    id: int
    token: str
    allowed_domains: Optional[list[str]]
    settings: Optional[dict]
    is_active: bool
    usage_count: int
    usage_limit: Optional[int]
    expires_at: Optional[datetime]
    created_at: datetime
    embed_script: str


@router.post("/{workflow_id}/embed-token")
async def create_or_update_embed_token(
    workflow_id: int,
    request: Request,
    embed_request: EmbedTokenRequest,
    user: UserModel = Depends(get_user),
) -> EmbedTokenResponse:
    """
    Create or update an embed token for a workflow.
    Each workflow can have only one active embed token.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Check if an embed token already exists for this workflow
    existing_tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=False
    )

    expires_at = None
    if embed_request.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=embed_request.expires_in_days)

    if existing_tokens:
        # Update the existing token (reactivate if needed)
        token = await db_client.update_embed_token(
            existing_tokens[0].id,
            user.selected_organization_id,
            allowed_domains=embed_request.allowed_domains,
            settings=embed_request.settings,
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
            is_active=True,
        )
    else:
        # Create new token
        token = await db_client.create_embed_token(
            workflow_id=workflow_id,
            organization_id=user.selected_organization_id,
            created_by=user.id,
            allowed_domains=embed_request.allowed_domains,
            settings=embed_request.settings,
            usage_limit=embed_request.usage_limit,
            expires_at=expires_at,
        )

    # Generate embed script
    embed_script = generate_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
    )


@router.get("/{workflow_id}/embed-token")
async def get_embed_token(
    workflow_id: int,
    request: Request,
    user: UserModel = Depends(get_user),
) -> Optional[EmbedTokenResponse]:
    """
    Get the embed token for a workflow if it exists.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get active embed tokens for this workflow
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=True
    )

    if not tokens:
        return None

    token = tokens[0]  # There should be only one active token per workflow

    # Generate embed script
    embed_script = generate_embed_script(token)

    return EmbedTokenResponse(
        id=token.id,
        token=token.token,
        allowed_domains=token.allowed_domains,
        settings=token.settings,
        is_active=token.is_active,
        usage_count=token.usage_count,
        usage_limit=token.usage_limit,
        expires_at=token.expires_at,
        created_at=token.created_at,
        embed_script=embed_script,
    )


@router.delete("/{workflow_id}/embed-token")
async def deactivate_embed_token(
    workflow_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """
    Deactivate the embed token for a workflow.
    """
    # Verify workflow exists and user has access
    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(
            status_code=404, detail=f"Workflow with id {workflow_id} not found"
        )

    # Get active embed tokens for this workflow
    tokens = await db_client.get_embed_tokens_by_workflow(
        workflow_id, user.selected_organization_id, active_only=True
    )

    if not tokens:
        raise HTTPException(
            status_code=404, detail="No active embed token found for this workflow"
        )

    # Deactivate the token
    success = await db_client.deactivate_embed_token(
        tokens[0].id, user.selected_organization_id
    )

    if success:
        return {"message": "Embed token deactivated successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to deactivate embed token")
