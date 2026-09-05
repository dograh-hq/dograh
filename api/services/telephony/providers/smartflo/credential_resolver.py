"""Smartflo credential resolution and security masking.

Resolution priority:
1. Request Body (call_details)
2. Agent configuration (workflow_configurations)
3. Organization configuration (telephony_configurations / organization_configurations)
4. Environment variables
"""

import os
from typing import Any, Dict, Optional, Tuple


def mask_phone_number(phone: Optional[str]) -> str:
    """Mask phone number for safe logging (e.g. 919999999999 -> 91******9999)."""
    if not phone:
        return "UNKNOWN"
    phone_str = str(phone).strip()
    if len(phone_str) <= 6:
        return "***"
    return f"{phone_str[:2]}******{phone_str[-4:]}"


def resolve_smartflo_credentials(
    call_details: Optional[Dict[str, Any]] = None,
    agent_config: Optional[Dict[str, Any]] = None,
    org_config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, str, str]:
    """
    Resolve Smartflo credentials according to strict priority order.

    Returns:
        Tuple of (click_to_call_api_key, did_number, jwt_token, api_domain)

    Raises:
        ValueError: If click_to_call_api_key or did_number is missing.
    """
    call_details = call_details or {}
    agent_config = agent_config or {}
    org_config = org_config or {}

    # 1. Click-to-Call API Key (Request > Org Config > Environment)
    click_to_call_api_key = (
        call_details.get("smartflo_api_key")
        or call_details.get("click_to_call_api_key")
        or org_config.get("click_to_call_api_key")
        or os.getenv("SMARTFLO_CLICK_TO_CALL_API_KEY")
    )

    # 2. DID Number / Caller ID (Request > Org Default Phone > Environment)
    from_numbers = org_config.get("from_numbers") or []
    first_from_number = from_numbers[0] if isinstance(from_numbers, list) and from_numbers else None

    did_number = (
        call_details.get("caller_id")
        or call_details.get("from_number")
        or call_details.get("smartflo_did_number")
        or org_config.get("default_from_number")
        or org_config.get("smartflo_did_number")
        or first_from_number
        or os.getenv("SMARTFLO_DID_NUMBER")
    )

    # 3. JWT Token (Request > Org Config > Environment)
    jwt_token = (
        call_details.get("smartflo_jwt_token")
        or org_config.get("smartflo_jwt_token")
        or os.getenv("SMARTFLO_JWT_TOKEN")
    )

    # 4. API Domain
    api_domain = (
        call_details.get("smartflo_api_domain")
        or org_config.get("smartflo_api_domain")
        or os.getenv("SMARTFLO_API_DOMAIN", "https://api-smartflo.tatateleservices.com")
    )

    if not click_to_call_api_key:
        raise ValueError("Missing required Smartflo Click-to-Call API Key")

    if not did_number:
        raise ValueError("Missing required Smartflo DID Number (caller_id)")

    api_domain = api_domain.rstrip("/")

    return (
        str(click_to_call_api_key).strip(),
        str(did_number).strip(),
        str(jwt_token or "").strip(),
        str(api_domain).strip(),
    )
