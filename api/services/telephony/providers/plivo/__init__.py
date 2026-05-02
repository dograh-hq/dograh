"""Plivo telephony provider package."""

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

from .config import PlivoConfigurationRequest, PlivoConfigurationResponse
from .provider import PlivoProvider
from .transport import create_transport

PLIVO_API_BASE_URL = "https://api.plivo.com/v1"


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "plivo",
        "auth_id": value.get("auth_id"),
        "auth_token": value.get("auth_token"),
        "application_id": value.get("application_id"),
        "from_numbers": value.get("from_numbers", []),
    }


async def _ensure_application_id(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-create a Plivo Application if one wasn't supplied.

    The application is created with our inbound dispatcher URL pre-set — the
    same URL ``configure_inbound`` would POST later — so inbound calls work
    immediately for any number bound to this application.
    """
    if credentials.get("application_id"):
        return credentials

    auth_id = credentials.get("auth_id")
    auth_token = credentials.get("auth_token")
    if not auth_id or not auth_token:
        return credentials

    backend_endpoint, _ = await get_backend_endpoints()
    inbound_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"

    app_name = f"dograh-{uuid.uuid4().hex[:12]}"
    endpoint = f"{PLIVO_API_BASE_URL}/Account/{auth_id}/Application/"
    body = {
        "app_name": app_name,
        "answer_url": inbound_url,
        "answer_method": "POST",
        "hangup_url": "",
    }
    auth = aiohttp.BasicAuth(auth_id, auth_token)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=body, auth=auth) as response:
                response_text = await response.text()
                if response.status not in (200, 201, 202):
                    logger.error(
                        f"[Plivo] applicationCreate failed: "
                        f"HTTP {response.status} body={response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            f"Failed to auto-create Plivo Application: "
                            f"HTTP {response.status} {response_text}"
                        ),
                    )
                data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[Plivo] applicationCreate transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Plivo to auto-create application: {e}",
        )

    created_id = data.get("app_id")
    if not created_id:
        logger.error(f"[Plivo] applicationCreate response missing app_id: {data}")
        raise HTTPException(
            status_code=502,
            detail=f"Plivo applicationCreate response missing app_id: {data}",
        )

    logger.info(
        f"[Plivo] auto-created Application '{app_name}' (id={created_id}) on "
        f"account {auth_id}"
    )
    return {**credentials, "application_id": str(created_id)}


_UI_METADATA = ProviderUIMetadata(
    display_name="Plivo",
    docs_url="https://docs.dograh.com/integrations/telephony/plivo",
    fields=[
        ProviderUIField(name="auth_id", label="Auth ID", type="text", sensitive=True),
        ProviderUIField(
            name="auth_token", label="Auth Token", type="password", sensitive=True
        ),
        ProviderUIField(
            name="application_id",
            label="Application ID",
            type="text",
            required=False,
            description=(
                "Plivo Application ID whose answer_url is updated when inbound "
                "workflows are attached to numbers on this account. Leave blank "
                "and we will auto-create one for you on save."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Plivo phone numbers used for outbound calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="plivo",
    provider_cls=PlivoProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=PlivoConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=PlivoConfigurationResponse,
    account_id_credential_field="auth_id",
    preprocess_credentials_on_save=_ensure_application_id,
)


register(SPEC)


__all__ = [
    "SPEC",
    "PlivoConfigurationRequest",
    "PlivoConfigurationResponse",
    "PlivoProvider",
    "create_transport",
]
