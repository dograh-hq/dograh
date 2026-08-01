"""Service layer for credential management operations and cache invalidation."""

from typing import Any, Optional
from urllib.parse import urlparse
from fastapi import HTTPException

from api.db import db_client
from api.db.models import ExternalCredentialModel
from api.enums import WebhookCredentialType
from api.utils.oauth2_token_cache import invalidate_token


def validate_credential_data(
    credential_type: WebhookCredentialType, credential_data: dict
) -> None:
    """Validate that credential_data matches the expected structure for the credential type.

    Args:
        credential_type: The type of credential
        credential_data: The credential data to validate

    Raises:
        HTTPException: If validation fails
    """
    if credential_type == WebhookCredentialType.NONE:
        # No data required
        return

    if credential_type == WebhookCredentialType.API_KEY:
        if "header_name" not in credential_data or "api_key" not in credential_data:
            raise HTTPException(
                status_code=400,
                detail="API Key credential requires 'header_name' and 'api_key' fields",
            )

    elif credential_type == WebhookCredentialType.BEARER_TOKEN:
        if "token" not in credential_data:
            raise HTTPException(
                status_code=400,
                detail="Bearer Token credential requires 'token' field",
            )

    elif credential_type == WebhookCredentialType.BASIC_AUTH:
        if "username" not in credential_data or "password" not in credential_data:
            raise HTTPException(
                status_code=400,
                detail="Basic Auth credential requires 'username' and 'password' fields",
            )

    elif credential_type == WebhookCredentialType.CUSTOM_HEADER:
        if (
            "header_name" not in credential_data
            or "header_value" not in credential_data
        ):
            raise HTTPException(
                status_code=400,
                detail="Custom Header credential requires 'header_name' and 'header_value' fields",
            )
            
    elif credential_type == WebhookCredentialType.OAUTH2_CLIENT_CREDENTIALS:
        required = {"client_id", "client_secret", "token_url"}
        missing_or_empty = [k for k in sorted(required) if not credential_data.get(k)]
        if missing_or_empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"OAuth2 Client Credentials requires non-empty fields: "
                    f"{', '.join(missing_or_empty)}"
                ),
            )
        token_url = str(credential_data.get("token_url", ""))
        parsed = urlparse(token_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise HTTPException(
                status_code=400,
                detail="token_url must use HTTPS and have a valid hostname",
            )


async def update_credential_with_invalidation(
    credential_uuid: str,
    organization_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    credential_type: Optional[WebhookCredentialType] = None,
    credential_data: Optional[dict[str, Any]] = None,
) -> Optional[ExternalCredentialModel]:
    """Validate, update a credential in the DB, and invalidate its OAuth token cache if needed.

    Reuses one scoped DB read for existence check, effective-type validation, and cache invalidation.
    """
    existing = await db_client.get_credential_by_uuid(credential_uuid, organization_id)
    if not existing:
        return None

    # Validate against effective credential_type and effective credential_data
    effective_type = credential_type if credential_type is not None else WebhookCredentialType(existing.credential_type)
    effective_data = credential_data if credential_data is not None else (existing.credential_data or {})
    validate_credential_data(effective_type, effective_data)

    type_str = credential_type.value if credential_type else None

    updated = await db_client.update_credential(
        credential_uuid=credential_uuid,
        organization_id=organization_id,
        name=name,
        description=description,
        credential_type=type_str,
        credential_data=credential_data,
    )

    if updated:
        if (
            existing.credential_type == "oauth2_client_credentials"
            or updated.credential_type == "oauth2_client_credentials"
        ):
            await invalidate_token(str(updated.credential_uuid))

    return updated


async def delete_credential_with_invalidation(
    credential_uuid: str,
    organization_id: int,
) -> bool:
    """Soft-delete a credential and invalidate its cached OAuth token if applicable."""
    credential = await db_client.get_credential_by_uuid(
        credential_uuid, organization_id
    )
    if credential and credential.credential_type == "oauth2_client_credentials":
        await invalidate_token(credential_uuid)

    return await db_client.delete_credential(credential_uuid, organization_id)
