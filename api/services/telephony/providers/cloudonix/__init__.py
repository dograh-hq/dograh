"""Cloudonix telephony provider package."""

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

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CLOUDONIX_API_BASE_URL, CloudonixProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "cloudonix",
        "bearer_token": value.get("bearer_token"),
        "api_key": value.get("api_key"),  # For x-cx-apikey validation
        "domain_id": value.get("domain_id"),
        "application_name": value.get("application_name"),
        "from_numbers": value.get("from_numbers", []),
    }


async def _ensure_application_name(credentials: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-create a Cloudonix Voice Application if one wasn't supplied.

    The application is created with our inbound dispatcher URL pre-set — the
    same URL ``configure_inbound`` would PATCH later — so inbound calls work
    immediately for any DNID bound to this application.
    """
    if credentials.get("application_name"):
        return credentials

    bearer_token = credentials.get("bearer_token")
    domain_id = credentials.get("domain_id")
    if not bearer_token or not domain_id:
        return credentials

    backend_endpoint, _ = await get_backend_endpoints()
    inbound_url = f"{backend_endpoint}/api/v1/telephony/inbound/run"

    name = f"dograh-{uuid.uuid4().hex[:12]}"
    endpoint = (
        f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/{domain_id}/applications"
    )
    body = {"name": name, "type": "cxml", "url": inbound_url, "method": "POST"}
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=body, headers=headers) as response:
                response_text = await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Cloudonix] applicationCreate failed: "
                        f"HTTP {response.status} body={response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=(
                            f"Failed to auto-create Cloudonix Voice Application: "
                            f"HTTP {response.status} {response_text}"
                        ),
                    )
                data = await response.json()
    except aiohttp.ClientError as e:
        logger.error(f"[Cloudonix] applicationCreate transport error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Cloudonix to auto-create application: {e}",
        )

    created_name = data.get("name") or name
    logger.info(
        f"[Cloudonix] auto-created Voice Application '{created_name}' on domain "
        f"{domain_id}"
    )
    return {**credentials, "application_name": created_name}


_UI_METADATA = ProviderUIMetadata(
    display_name="Cloudonix",
    docs_url="https://docs.dograh.com/integrations/telephony/cloudonix",
    fields=[
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            sensitive=True,
            description="Cloudonix API Bearer Token",
        ),
        ProviderUIField(name="domain_id", label="Domain ID", type="text"),
        ProviderUIField(
            name="application_name",
            label="Application Name",
            type="text",
            required=False,
            description=(
                "Cloudonix Voice Application name whose url is updated when "
                "inbound workflows are attached to numbers on this domain. "
                "Leave blank and we will auto-create one for you on save."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
        ),
    ],
)


SPEC = ProviderSpec(
    name="cloudonix",
    provider_cls=CloudonixProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=CloudonixConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=CloudonixConfigurationResponse,
    account_id_credential_field="domain_id",
    preprocess_credentials_on_save=_ensure_application_name,
)


register(SPEC)


__all__ = [
    "SPEC",
    "CloudonixConfigurationRequest",
    "CloudonixConfigurationResponse",
    "CloudonixProvider",
    "create_transport",
]
