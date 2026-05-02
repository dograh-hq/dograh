"""Vobiz telephony provider package."""

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

from .config import VobizConfigurationRequest, VobizConfigurationResponse
from .provider import VobizProvider
from .transport import create_transport

VOBIZ_API_BASE_URL = "https://api.vobiz.ai/api"


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "vobiz",
        "auth_id": value.get("auth_id"),
        "auth_token": value.get("auth_token"),
        "application_id": value.get("application_id"),
        "from_numbers": value.get("from_numbers", []),
    }


async def _ensure_application_id(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-create a Vobiz Application if one wasn't supplied.

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
    endpoint = f"{VOBIZ_API_BASE_URL}/v1/Account/{auth_id}/Application/"
    body = {
        "app_name": app_name,
        "answer_url": inbound_url,
        "answer_method": "POST",
        "hangup_url": "",
    }
    headers = {
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=body, headers=headers) as response:
                response_text = await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Vobiz] applicationCreate failed: "
                        f"HTTP {response.status} body={response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            f"Failed to auto-create Vobiz Application: "
                            f"HTTP {response.status} {response_text}"
                        ),
                    )
                data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[Vobiz] applicationCreate transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Vobiz to auto-create application: {e}",
        )

    created_id = data.get("app_id")
    if not created_id:
        logger.error(f"[Vobiz] applicationCreate response missing app_id: {data}")
        raise HTTPException(
            status_code=502,
            detail=f"Vobiz applicationCreate response missing app_id: {data}",
        )

    logger.info(
        f"[Vobiz] auto-created Application '{app_name}' (id={created_id}) on "
        f"account {auth_id}"
    )
    return {**credentials, "application_id": str(created_id)}


_UI_METADATA = ProviderUIMetadata(
    display_name="Vobiz",
    docs_url="https://docs.dograh.com/integrations/telephony/vobiz",
    fields=[
        ProviderUIField(
            name="auth_id",
            label="Account ID",
            type="text",
            sensitive=True,
            description="Vobiz Account ID (e.g., MA_SYQRLN1K)",
        ),
        ProviderUIField(
            name="auth_token", label="Auth Token", type="password", sensitive=True
        ),
        ProviderUIField(
            name="application_id",
            label="Application ID",
            type="text",
            required=False,
            description=(
                "Vobiz Application ID whose answer_url is updated when "
                "inbound workflows are attached to numbers on this account. "
                "Leave blank and we will auto-create one for you on save."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted phone numbers without + prefix",
        ),
    ],
)


SPEC = ProviderSpec(
    name="vobiz",
    provider_cls=VobizProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VobizConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VobizConfigurationResponse,
    account_id_credential_field="auth_id",
    preprocess_credentials_on_save=_ensure_application_id,
)


register(SPEC)


__all__ = [
    "SPEC",
    "VobizConfigurationRequest",
    "VobizConfigurationResponse",
    "VobizProvider",
    "create_transport",
]
