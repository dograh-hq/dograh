"""Build HTTP authentication headers from ExternalCredentialModel.

This module provides functions for constructing HTTP authentication headers
from ExternalCredentialModel instances. Used by both webhook integrations
and custom tool execution.
"""

import base64
from typing import TYPE_CHECKING, Any, Dict, Optional

from loguru import logger

if TYPE_CHECKING:
    from api.db.models import ExternalCredentialModel


async def build_auth_header(
    credential: "ExternalCredentialModel",
    force_refresh: bool = False,
) -> Dict[str, str]:
    """Build authentication headers for a given ExternalCredentialModel.

    Args:
        credential: The ExternalCredentialModel instance
        force_refresh: If True (e.g. retry after 401), force a fresh token fetch.

    Returns:
        Dict of header name to value (e.g. {"Authorization": "Bearer ..."})

    Raises:
        ValueError: If credential model is missing required fields or token fetch fails.
    """
    cred_type = credential.credential_type
    cred_data = credential.credential_data or {}
    cred_uuid = getattr(credential, "credential_uuid", None)

    if cred_type == "bearer_token":
        token = cred_data.get("token", "")
        return {"Authorization": f"Bearer {token}"}

    elif cred_type == "api_key":
        header_name = cred_data.get("header_name", "X-API-Key")
        api_key = cred_data.get("api_key", "")
        return {header_name: api_key}

    elif cred_type == "basic_auth":
        username = cred_data.get("username", "")
        password = cred_data.get("password", "")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    elif cred_type == "custom_header":
        header_name = cred_data.get("header_name", "X-Custom")
        header_value = cred_data.get("header_value", "")
        return {header_name: header_value}

    elif cred_type == "oauth2_client_credentials":
        from api.services.oauth2_token_cache import get_or_fetch_token

        token_url = cred_data.get("token_url")
        client_id = cred_data.get("client_id")
        client_secret = cred_data.get("client_secret")
        scope = cred_data.get("scope") or None
        audience = cred_data.get("audience") or None

        if not (token_url and client_id and client_secret and cred_uuid):
            raise ValueError(
                f"Missing required OAuth2 fields for credential {cred_uuid}. "
                f"Requires token_url, client_id, and client_secret."
            )

        try:
            token = await get_or_fetch_token(
                credential_uuid=str(cred_uuid),
                client_id=client_id,
                client_secret=client_secret,
                token_url=token_url,
                scope=scope,
                audience=audience,
                force_refresh=force_refresh,
            )
            return {"Authorization": f"Bearer {token}"}
        except ValueError:
            # Re-raise so callers (webhook delivery, tool execution) can treat
            # this as a retryable failure rather than silently sending without auth.
            raise
        except Exception as e:
            # Unexpected errors (e.g. import errors) — re-raise as ValueError
            # so the caller interface is consistent.
            raise ValueError(
                f"Failed to fetch OAuth2 token for {cred_uuid}: {e}"
            ) from e

    return {}


async def invalidate_and_rebuild_auth(
    credential: "ExternalCredentialModel",
) -> Dict[str, str]:
    """Invalidate cached OAuth2 token and rebuild auth headers for retry after 401."""
    if getattr(credential, "credential_type", None) == "oauth2_client_credentials":
        from api.services.oauth2_token_cache import invalidate_token

        cred_uuid = getattr(credential, "credential_uuid", None)
        if cred_uuid:
            await invalidate_token(str(cred_uuid))
    return await build_auth_header(credential, force_refresh=True)


async def rebuild_headers_after_401(
    credential: Optional["ExternalCredentialModel"],
    headers: Dict[str, str],
) -> Dict[str, str]:
    """If credential is an OAuth2 client credential, invalidate token and rebuild headers.

    Purges stale headers matching fresh credential header names case-insensitively.
    Returns the dictionary of fresh credential headers added, or an empty dict.
    Raises ValueError on token refresh failure.
    """
    if (
        credential is not None
        and getattr(credential, "credential_type", None) == "oauth2_client_credentials"
    ):
        credential_headers = await invalidate_and_rebuild_auth(credential)
        if credential_headers:
            for fresh_key in credential_headers:
                for existing_key in list(headers):
                    if existing_key.lower() == fresh_key.lower():
                        del headers[existing_key]
            headers.update(credential_headers)
        return credential_headers
    return {}


def build_auth_header_from_data(
    credential_type: str,
    credential_data: Optional[Dict[str, Any]] = None,
    credential_uuid: Optional[str] = None,
) -> Dict[str, str]:
    """Build authentication header from raw credential data.

    This is a convenience function when you have credential data
    directly rather than a full ExternalCredentialModel.

    Note: This function is synchronous and does NOT support
    oauth2_client_credentials (which requires async Redis/HTTP).
    It is primarily used in unit tests. Production code must
    always use the async `build_auth_header` function.

    Args:
        credential_type: Type of credential (bearer_token, api_key, etc.)
        credential_data: Dict containing credential-specific fields
        credential_uuid: Optional UUID required for OAuth2 caching

    Returns:
        Dict with header name and value
    """
    cred_data = credential_data or {}

    if credential_type == "bearer_token":
        token = cred_data.get("token", "")
        return {"Authorization": f"Bearer {token}"}

    elif credential_type == "api_key":
        header_name = cred_data.get("header_name", "X-API-Key")
        api_key = cred_data.get("api_key", "")
        return {header_name: api_key}

    elif credential_type == "basic_auth":
        username = cred_data.get("username", "")
        password = cred_data.get("password", "")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    elif credential_type == "custom_header":
        header_name = cred_data.get("header_name", "X-Custom")
        header_value = cred_data.get("header_value", "")
        return {header_name: header_value}

    elif credential_type == "oauth2_client_credentials":
        logger.warning(
            "build_auth_header_from_data does not support oauth2_client_credentials. "
            "Use async build_auth_header instead."
        )
        return {}

    return {}
