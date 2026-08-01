"""Service layer for credential management operations and cache invalidation."""

from typing import Any, Optional
from fastapi import HTTPException

from api.db import db_client
from api.db.models import ExternalCredentialModel
from api.enums import WebhookCredentialType
from api.utils.oauth2_token_cache import invalidate_token


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
    from api.routes.credentials import validate_credential_data
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
