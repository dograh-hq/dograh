"""Telnyx telephony provider package."""

import uuid
from typing import Any, Dict

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)
from api.utils.common import get_backend_endpoints

from .config import TelnyxConfigurationRequest, TelnyxConfigurationResponse
from .provider import TelnyxProvider
from .transport import create_transport

TELNYX_API_BASE_URL = "https://api.telnyx.com/v2"


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "telnyx",
        "api_key": value.get("api_key"),
        "connection_id": value.get("connection_id"),
        "webhook_public_key": value.get("webhook_public_key"),
        "from_numbers": value.get("from_numbers", []),
    }


async def _ensure_connection_id(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-create a Telnyx Call Control Application if one wasn't supplied.

    The application is created with our inbound dispatcher URL pre-set on
    ``webhook_event_url`` — the same URL ``configure_inbound`` would PATCH
    later — so inbound calls work immediately for any number bound to this
    application.
    """
    if credentials.get("connection_id"):
        return credentials

    api_key = credentials.get("api_key")
    if not api_key:
        return credentials

    backend_endpoint, _ = await get_backend_endpoints()
    inbound_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"

    application_name = f"dograh-{uuid.uuid4().hex[:12]}"
    endpoint = f"{TELNYX_API_BASE_URL}/call_control_applications"
    body = {
        "application_name": application_name,
        "webhook_event_url": inbound_url,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=body, headers=headers) as response:
                response_text = await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Telnyx] callControlApplicationCreate failed: "
                        f"HTTP {response.status} body={response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            f"Failed to auto-create Telnyx Call Control "
                            f"Application: HTTP {response.status} "
                            f"{response_text}"
                        ),
                    )
                payload = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[Telnyx] callControlApplicationCreate transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Failed to reach Telnyx to auto-create Call Control Application: {e}"
            ),
        )

    created_id = (payload.get("data") or {}).get("id")
    if not created_id:
        logger.error(
            f"[Telnyx] callControlApplicationCreate response missing data.id: {payload}"
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"Telnyx callControlApplicationCreate response missing "
                f"data.id: {payload}"
            ),
        )

    logger.info(
        f"[Telnyx] auto-created Call Control Application "
        f"'{application_name}' (id={created_id})"
    )
    return {**credentials, "connection_id": str(created_id)}


_UI_METADATA = ProviderUIMetadata(
    display_name="Telnyx",
    docs_url="https://docs.dograh.com/integrations/telephony/telnyx",
    fields=[
        ProviderUIField(
            name="api_key", label="API Key", type="password", sensitive=True
        ),
        ProviderUIField(
            name="connection_id",
            label="Call Control App ID",
            type="text",
            required=False,
            description=(
                "Telnyx Call Control Application ID (connection_id). Leave "
                "blank and we will auto-create one for you on save."
            ),
        ),
        ProviderUIField(
            name="webhook_public_key",
            label="Webhook Public Key",
            type="textarea",
            required=False,
            sensitive=False,
            description=(
                "Public key from Mission Control Portal → Keys & Credentials "
                "→ Public Key. Used to verify Telnyx webhook signatures. "
                "Without it, webhooks from Telnyx will be rejected."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Telnyx phone numbers",
        ),
    ],
)


SPEC = ProviderSpec(
    name="telnyx",
    provider_cls=TelnyxProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=TelnyxConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=TelnyxConfigurationResponse,
    account_id_credential_field="connection_id",
    preprocess_credentials_on_save=_ensure_connection_id,
)


register(SPEC)


__all__ = [
    "SPEC",
    "TelnyxConfigurationRequest",
    "TelnyxConfigurationResponse",
    "TelnyxProvider",
    "create_transport",
]
