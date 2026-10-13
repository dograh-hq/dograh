"""WhatsApp telephony provider package.

Inbound only: a WhatsApp user calls the business number, Meta announces the
call on the webhook in ``routes.py`` with an SDP offer, and media runs over
WebRTC. Nothing here can dial out, and the spec says so.
"""

import secrets
from typing import Any, Dict

from fastapi import HTTPException

from api.services.telephony.base import ProviderPhoneNumberLookupError
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import WhatsAppConfigurationRequest
from .provider import WhatsAppProvider


async def _preprocess_credentials_on_save(
    credentials: Dict[str, Any],
    existing_credentials: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Check the credentials with Meta, keep the number unique, own the verify token.

    Meta is asked which number sits behind the Phone Number ID, using the
    access token being saved. A typo or a token without access to the number
    fails here rather than on the first call, and no organization can
    register a Phone Number ID it holds no token for.

    The Phone Number ID must also be held by exactly one active
    configuration. Meta's webhook carries no tenant, so inbound routing
    refuses to choose between two configurations claiming one number; a
    second save, even by the rightful owner, would silence the first.

    The verify token is minted on create and carried on update. Meta stores
    the token the customer pasted and replays it on every verification
    handshake, so an update must never mint a new one — the saved webhook
    would silently stop verifying. The value is always written from here and
    never read from the request.
    """
    try:
        await WhatsAppProvider(credentials).get_available_phone_numbers()
    except ProviderPhoneNumberLookupError as e:
        if _meta_rejected(e.status_code):
            raise HTTPException(
                status_code=400,
                detail=f"Meta did not accept these credentials: {e}",
            ) from e
        raise HTTPException(
            status_code=502,
            detail=f"Could not verify these credentials with Meta: {e}",
        ) from e

    phone_number_id = str(credentials.get("phone_number_id") or "")
    previous_phone_number_id = str(
        (existing_credentials or {}).get("phone_number_id") or ""
    )
    if phone_number_id != previous_phone_number_id:
        # Imported here so the provider package, like every other one, stays
        # free of the database layer at import time.
        from api.db import db_client

        if await db_client.list_active_whatsapp_configurations_by_phone_number_id(
            phone_number_id
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another active WhatsApp configuration already uses this "
                    "Phone Number ID. Each Meta number can be configured once."
                ),
            )

    credentials = dict(credentials)
    existing_token = (existing_credentials or {}).get("webhook_verify_token")
    credentials["webhook_verify_token"] = existing_token or secrets.token_urlsafe(32)
    return credentials


def _meta_rejected(status_code: int | None) -> bool:
    """Meta turned the request down, as opposed to failing or throttling it."""
    return (
        status_code is not None
        and 400 <= status_code < 500
        and status_code not in (408, 429)
    )


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "whatsapp",
        "access_token": value.get("access_token"),
        "phone_number_id": value.get("phone_number_id"),
        "app_secret": value.get("app_secret"),
        "from_numbers": value.get("from_numbers", []),
    }


async def _no_media_websocket(*args: Any, **kwargs: Any) -> Any:
    """WhatsApp media runs over WebRTC (see ``routes.py``), not a websocket."""
    raise NotImplementedError("WhatsApp calls do not use a media websocket")


_UI_METADATA = ProviderUIMetadata(
    display_name="WhatsApp",
    docs_url="https://docs.dograh.com/integrations/telephony/whatsapp",
    fields=[
        ProviderUIField(
            name="access_token",
            label="Access Token",
            type="password",
            sensitive=True,
            description="System user access token from Meta Business settings",
        ),
        ProviderUIField(
            name="phone_number_id",
            label="Phone Number ID",
            type="text",
            description="From your Meta app: WhatsApp → API Setup",
        ),
        ProviderUIField(
            name="app_secret",
            label="App Secret",
            type="password",
            sensitive=True,
            description="From your Meta app: App settings → Basic",
        ),
        ProviderUIField(
            name="webhook_verify_token",
            label="Webhook Verify Token",
            type="readonly",
            required=False,
            description=(
                "Generated for you. Paste it, with the Callback URL from the "
                "configuration page, into your Meta app's webhook settings."
            ),
        ),
    ],
)


SPEC = ProviderSpec(
    name="whatsapp",
    provider_cls=WhatsAppProvider,
    config_loader=_config_loader,
    transport_factory=_no_media_websocket,
    transport_sample_rate=16000,
    config_request_cls=WhatsAppConfigurationRequest,
    ui_metadata=_UI_METADATA,
    # Meta's webhook names the business number by this ID.
    account_id_credential_field="phone_number_id",
    # Assigns webhook_verify_token. Deliberately not listed in
    # server_managed_credential_fields: those are dropped from the
    # configuration response, and this one has to stay readable so the customer
    # can paste it into Meta.
    preprocess_credentials_on_save=_preprocess_credentials_on_save,
    # Meta only delivers calls placed *to* the business number.
    supports_outbound=False,
)


register(SPEC)


__all__ = [
    "SPEC",
    "WhatsAppConfigurationRequest",
    "WhatsAppProvider",
]
