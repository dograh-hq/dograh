"""Telnyx telephony provider package."""

import asyncio
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

from .config import TelnyxConfigurationRequest
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


async def _fetch_first_outbound_voice_profile_id(
    session: aiohttp.ClientSession,
    headers: Dict[str, str],
) -> str | None:
    """Return the account's first Outbound Voice Profile id, or None.

    Telnyx refuses outbound dialing from a Call Control Application with no
    Outbound Voice Profile attached (error D38), so the auto-created
    application is enriched with an existing profile when one is available.
    Profiles are never created here — they are a billing-affecting resource.

    Failures to list profiles are logged and swallowed: the config save must
    still succeed so inbound calls work either way.
    """
    endpoint = f"{TELNYX_API_BASE_URL}/outbound_voice_profiles"
    try:
        async with session.get(endpoint, headers=headers) as response:
            if response.status != 200:
                response_text = await response.text()
                logger.error(
                    f"[Telnyx] outboundVoiceProfileList failed: "
                    f"HTTP {response.status} body={response_text}"
                )
                return None
            payload = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"[Telnyx] outboundVoiceProfileList transport error: {e}")
        return None

    # Defensively tolerate any payload shape the API might return: a listing
    # failure must never abort the config save, so anything unexpected is
    # treated the same as "no profiles available".
    if not isinstance(payload, dict):
        logger.warning(
            f"[Telnyx] outboundVoiceProfileList unexpected payload type: "
            f"{type(payload).__name__}"
        )
        return None
    profiles = payload.get("data") or []
    if not isinstance(profiles, list):
        logger.warning(
            "[Telnyx] outboundVoiceProfileList 'data' is not a list; "
            "treating as no profiles available"
        )
        return None
    first = profiles[0] if profiles else None
    if not isinstance(first, dict):
        return None
    profile_id = first.get("id")
    return str(profile_id) if profile_id is not None else None


async def _ensure_connection_id(
    credentials: Dict[str, Any],
    existing_credentials: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Auto-create a Telnyx Call Control Application if one wasn't supplied.

    The application is created with our inbound dispatcher URL pre-set on
    ``webhook_event_url`` — the same URL ``configure_inbound`` would PATCH
    later — so inbound calls work immediately for any number bound to this
    application.

    When the account already has an Outbound Voice Profile, it is attached
    via the nested ``outbound.outbound_voice_profile_id`` field so the first
    outbound call does not fail with Telnyx error D38 ("Connection has no
    Outbound Profile assigned"). Without one, the application is still
    created and saved — inbound is unaffected — and a warning is logged
    pointing at Mission Control. Profile listing failures never break the
    save for the same reason.
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
            outbound_profile_id = await _fetch_first_outbound_voice_profile_id(
                session, headers
            )
            if outbound_profile_id:
                body["outbound"] = {
                    "outbound_voice_profile_id": outbound_profile_id
                }
            else:
                logger.warning(
                    "[Telnyx] Auto-created Call Control Application will "
                    "have no Outbound Voice Profile; outbound calls will "
                    "fail with Telnyx error D38 until one is assigned in "
                    "Mission Control. Inbound calls are unaffected."
                )
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
    account_id_credential_field="connection_id",
    preprocess_credentials_on_save=_ensure_connection_id,
)


register(SPEC)


__all__ = [
    "SPEC",
    "TelnyxConfigurationRequest",
    "TelnyxProvider",
    "create_transport",
]
