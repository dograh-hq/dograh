"""Build HTTP authentication headers from ExternalCredentialModel.

This module provides functions for constructing HTTP authentication headers
from ExternalCredentialModel instances. Used by both webhook integrations
and custom tool execution.
"""

import base64
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from api.db.models import ExternalCredentialModel


def build_auth_header(credential: "ExternalCredentialModel") -> Dict[str, str]:
    """Build authentication header based on credential type.

    Supports the following credential types:
    - bearer_token: Authorization: Bearer <token>
    - api_key: Custom header with API key
    - basic_auth: Authorization: Basic <base64(username:password)>
    - custom_header: Any custom header name/value pair

    Args:
        credential: The ExternalCredentialModel instance

    Returns:
        Dict with header name and value, or empty dict if credential type
        is not recognized or is 'none'
    """
    cred_type = credential.credential_type
    cred_data = credential.credential_data or {}

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

    return {}


def build_auth_header_from_data(
    credential_type: str,
    credential_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Build authentication header from raw credential data.

    This is a convenience function when you have credential data
    directly rather than a full ExternalCredentialModel.

    Args:
        credential_type: Type of credential (bearer_token, api_key, etc.)
        credential_data: Dict containing credential-specific fields

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

    return {}
