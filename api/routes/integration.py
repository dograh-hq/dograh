"""
Route for 3rd party integrations. Currently being backed by nango.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.integrations.nango import nango_service

router = APIRouter(prefix="/integration")


@dataclass
class IntegrationResponse:
    id: int
    integration_id: str
    organisation_id: int
    created_by: Optional[int]
    provider: str
    is_active: bool
    created_at: str
    action: str
    provider_data: dict


class SessionResponse(TypedDict):
    session_token: str
    expires_at: str


class WebhookResponse(TypedDict):
    status: str
    message: str


class UpdateIntegrationRequest(BaseModel):
    selected_files: List[Dict[str, Any]]


class AccessTokenResponse(BaseModel):
    access_token: Optional[str]
    refresh_token: Optional[str]
    expires_at: Optional[str]
    connection_id: str


def build_integration_response(integration) -> IntegrationResponse:
    """Build a standardized integration response with provider-specific data."""
    provider_data = {}

    if integration.provider == "google-sheet":
        # For Google Sheets, include selected_files
        provider_data["selected_files"] = integration.connection_details.get(
            "selected_files", []
        )
    elif integration.provider == "slack":
        # For Slack, include channel information
        channel = integration.connection_details.get("connection_config", {}).get(
            "incoming_webhook.channel"
        )
        if channel:
            provider_data["channel"] = channel

    return IntegrationResponse(
        id=integration.id,
        integration_id=integration.integration_id,
        organisation_id=integration.organisation_id,
        created_by=integration.created_by,
        provider=integration.provider,
        is_active=integration.is_active,
        created_at=integration.created_at.isoformat(),
        action=integration.action,
        provider_data=provider_data,
    )


@router.get("/")
async def get_integrations(
    user: UserModel = Depends(get_user),
) -> list[IntegrationResponse]:
    """
    Get all integrations for the user's selected organization.

    Returns:
        List of integrations associated with the user's selected organization
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    integrations = await db_client.get_integrations_by_organization_id(
        user.selected_organization_id
    )

    return [build_integration_response(integration) for integration in integrations]


@router.post("/session")
async def create_session(
    user: UserModel = Depends(get_user),
) -> SessionResponse:
    """
    Create a Nango session for the user's selected organization.

    Returns:
        Session token and ID for the created session
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    try:
        session_data = await nango_service.create_session(
            user_id=str(user.id), organization_id=user.selected_organization_id
        )

        return {
            "session_token": session_data["data"]["token"],
            "expires_at": session_data["data"]["expires_at"],
        }
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to create session: {str(e)}"
        )


@router.put("/{integration_id}")
async def update_integration(
    integration_id: int,
    request: UpdateIntegrationRequest,
    user: UserModel = Depends(get_user),
) -> IntegrationResponse:
    """
    Update an integration's selected files (for Google Sheets).

    Args:
        integration_id: The ID of the integration to update
        request: The update request containing selected files
        user: The authenticated user

    Returns:
        Updated integration details
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    # Get the integration first to verify ownership
    integrations = await db_client.get_integrations_by_organization_id(
        user.selected_organization_id
    )

    integration = next((i for i in integrations if i.id == integration_id), None)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Only allow updating selected_files for google-sheet provider
    if integration.provider != "google-sheet":
        raise HTTPException(
            status_code=400,
            detail="This endpoint only supports updating Google Sheet integrations",
        )

    # Update the connection_details with the new selected_files
    updated_connection_details = integration.connection_details.copy()
    updated_connection_details["selected_files"] = request.selected_files

    # Update the integration
    updated_integration = await db_client.update_integration_connection_details(
        integration_id=integration_id, connection_details=updated_connection_details
    )

    if not updated_integration:
        raise HTTPException(status_code=500, detail="Failed to update integration")

    return build_integration_response(updated_integration)


@router.get("/{integration_id}/access-token")
async def get_integration_access_token(
    integration_id: int,
    user: UserModel = Depends(get_user),
) -> AccessTokenResponse:
    """
    Get the latest access token for an integration from Nango.

    Args:
        integration_id: The ID of the integration
        user: The authenticated user

    Returns:
        Dict containing access token and expiration info
    """
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )

    # Get the integration to verify ownership and get connection details
    integrations = await db_client.get_integrations_by_organization_id(
        user.selected_organization_id
    )

    integration = next((i for i in integrations if i.id == integration_id), None)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    try:
        # Fetch the latest access token from Nango
        token_data = await nango_service.get_access_token(
            connection_id=integration.integration_id,
            provider_config_key=integration.provider,
        )

        # Extract relevant fields
        return AccessTokenResponse(
            access_token=token_data.get("credentials", {}).get("access_token"),
            refresh_token=token_data.get("credentials", {}).get("refresh_token"),
            expires_at=token_data.get("credentials", {}).get("expires_at"),
            connection_id=integration.integration_id,
        )

    except Exception as e:
        logger.error(f"Failed to get access token: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch access token: {str(e)}"
        )


@router.post("/webhook", include_in_schema=False)
async def handle_nango_webhook(
    request: Request,
) -> WebhookResponse:
    """
    Handle Nango integration webhook requests.

    Processes webhook events from Nango when integrations are created/updated
    and stores the integration details in the database.

    Args:
        request: The raw FastAPI request object

    Returns:
        WebhookResponse with status and message
    """
    raw_body = await request.body()

    # Get signature from headers (you may need to adjust the header name)
    signature = request.headers.get("X-Nango-Signature")

    # Use the nango service to process the webhook
    result = await nango_service.process_webhook(raw_body, signature)

    return result
