"""Service layer for credential management operations and cache invalidation."""

from typing import Any, Optional

from api.db import db_client
from api.db.models import ExternalCredentialModel
from api.utils.oauth2_token_cache import invalidate_token


async def update_credential_with_invalidation(
    credential_uuid: str,
    organization_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    credential_type: Optional[str] = None,
    credential_data: Optional[dict[str, Any]] = None,
    existing_type: Optional[str] = None,
) -> Optional[ExternalCredentialModel]:
    """Update a credential in the DB and invalidate its OAuth token cache if needed.

    Purges cached OAuth tokens if either the previous or the new type is OAuth2.
    """
    updated = await db_client.update_credential(
        credential_uuid=credential_uuid,
        organization_id=organization_id,
        name=name,
        description=description,
        credential_type=credential_type,
        credential_data=credential_data,
    )

    if updated:
        if (
            existing_type == "oauth2_client_credentials"
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
