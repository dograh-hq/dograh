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


async def build_auth_header(credential: "ExternalCredentialModel") -> Dict[str, str]:
    """Build authentication header based on credential type.

    Supports the following credential types:
    - bearer_token: Authorization: Bearer <token>
    - api_key: Custom header with API key
    - basic_auth: Authorization: Basic <base64(username:password)>
    - custom_header: Any custom header name/value pair
    - oauth2_client_credentials: Authorization: Bearer <cached_or_new_token>

    Args:
        credential: The ExternalCredentialModel instance

    Returns:
        Dict with header name and value, or empty dict if credential type
        is not recognized or is 'none'
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
        from api.utils.oauth2_token_cache import get_or_fetch_token
        token_url = cred_data.get("token_url")
        client_id = cred_data.get("client_id")
        client_secret = cred_data.get("client_secret")
        scope = cred_data.get("scope") or None
        audience = cred_data.get("audience") or None

        if not (token_url and client_id and client_secret and cred_uuid):
            logger.error(
                f"Missing required OAuth2 fields for credential {cred_uuid}. "
                f"Requires token_url, client_id, and client_secret."
            )
            return {}

        try:
            token = await get_or_fetch_token(
                credential_uuid=str(cred_uuid),
                client_id=client_id,
                client_secret=client_secret,
                token_url=token_url,
                scope=scope,
                audience=audience,
            )
            return {"Authorization": f"Bearer {token}"}
        except ValueError:
            # Re-raise so callers (webhook delivery, tool execution) can treat
            # this as a retryable failure rather than silently sending without auth.
            raise
        except Exception as e:
            # Unexpected errors (e.g. import errors) — re-raise as ValueError
            # so the caller interface is consistent.
            raise ValueError(f"Failed to fetch OAuth2 token for {cred_uuid}: {e}") from e

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
