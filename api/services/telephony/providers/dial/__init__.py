"""Dial telephony provider package.

Dial (https://getdial.ai) gives AI agents real US phone numbers. This
integration runs the call audio through Dial's Self-Hosted audio mode
(https://docs.getdial.ai/documentation/platform/self-hosted): Dial bridges
the phone call and streams G.711 μ-law audio both ways over a WebSocket that
Dograh hosts.

The customer only pastes an API key. On save, ``_ensure_dial_setup``:

1. resolves the Dial account id (the inbound routing key);
2. points the account's Self-Hosted audio mode at Dograh's media endpoint
   and activates it;
3. stores the Self-Hosted audio signing secret, used to verify each socket;
4. subscribes Dograh to ``call.ended`` so calls that never connect (busy, no
   answer, failed) still close their workflow runs.
"""

from typing import Any, Dict

from fastapi import HTTPException
from loguru import logger

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)
from api.utils.common import get_backend_endpoints

from .client import DialAPIError, DialClient
from .config import DialConfigurationRequest
from .provider import DialProvider
from .serializers import DIAL_AUDIO_FORMAT, DIAL_SAMPLE_RATE
from .transport import create_transport

_WEBHOOK_EVENT_TYPES = ["call.ended"]

_SERVER_MANAGED_FIELDS = (
    "account_id",
    "audio_signing_secret",
    "webhook_id",
    "webhook_secret",
)

_SELF_HOSTED_ACCESS_USE_CASE = (
    "Voice agents built on Dograh (https://dograh.com), an open-source voice "
    "AI platform. Dograh runs the speech pipeline and receives the raw call "
    "audio through Self-Hosted audio mode."
)


def media_socket_base_url(wss_backend_endpoint: str, account_id: str) -> str:
    """The Self-Hosted ``wsUrl``; Dial appends ``/<call_id>`` per call."""
    return f"{wss_backend_endpoint}/api/v1/telephony/dial/ws/{account_id}"


def events_url(backend_endpoint: str, account_id: str) -> str:
    return f"{backend_endpoint}/api/v1/telephony/dial/events/{account_id}"


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "dial",
        "api_key": value.get("api_key"),
        "account_id": value.get("account_id"),
        "audio_signing_secret": value.get("audio_signing_secret"),
        "webhook_id": value.get("webhook_id"),
        "webhook_secret": value.get("webhook_secret"),
    }


def _setup_error(message: str, error: Exception | None = None) -> HTTPException:
    if error is not None:
        logger.warning(f"[Dial] setup failed: {message}: {error}")
    return HTTPException(status_code=400, detail=message)


async def _require_self_hosted_access(client: DialClient) -> None:
    """Self-Hosted mode is approved per Dial account by a human reviewer.

    An account that never asked gets a request filed on its behalf, so the
    customer's only remaining step is to wait for approval and save again.
    """
    state = await client.get_self_hosted()
    access = state.get("access")
    if access == "granted":
        return

    if access in (None, "none"):
        try:
            await client.request_self_hosted_access(
                _SELF_HOSTED_ACCESS_USE_CASE, "https://dograh.com"
            )
        except DialAPIError as e:
            raise _setup_error(
                "Dial streams call audio to Dograh through its Self-Hosted mode, "
                "which needs approval on your Dial account. Request it at "
                "https://getdial.ai/dashboard/self-hosted, then save again.",
                e,
            ) from e
        raise _setup_error(
            "Dial streams call audio to Dograh through its Self-Hosted mode, "
            "which is approved per account. We've requested access for your "
            "Dial account; save this configuration again once it is granted "
            "(see https://getdial.ai/dashboard/self-hosted)."
        )

    if access == "pending":
        raise _setup_error(
            "Your Dial account's Self-Hosted access request is still under "
            "review. Save this configuration again once it is granted."
        )

    note = (state.get("accessRequest") or {}).get("reviewNote")
    raise _setup_error(
        "Dial declined Self-Hosted access for this account"
        + (f": {note}" if note else ".")
        + " Submit a new request at https://getdial.ai/dashboard/self-hosted."
    )


async def _ensure_webhook(
    client: DialClient, credentials: Dict[str, Any], target_url: str
) -> Dict[str, Any]:
    """Reuse this account's Dograh webhook if it exists, else create it.

    A webhook is reused only when it already targets ``target_url``; the
    stored id alone is not enough, because the backend URL can change between
    saves. When the stored webhook points somewhere else it was ours, so it is
    removed rather than left delivering to a dead endpoint.
    """
    webhooks = await client.list_webhooks()
    stored_id = credentials.get("webhook_id")

    existing = next(
        (
            w
            for w in webhooks
            if w.get("id") == stored_id and w.get("targetUrl") == target_url
        ),
        None,
    ) or next((w for w in webhooks if w.get("targetUrl") == target_url), None)

    stale = next(
        (
            w
            for w in webhooks
            if w.get("id") == stored_id and w.get("targetUrl") != target_url
        ),
        None,
    )
    if stale:
        try:
            await client.delete_webhook(stale["id"])
            logger.info(
                f"[Dial] removed call-status webhook {stale['id']} for an old URL"
            )
        except DialAPIError as e:
            logger.warning(f"[Dial] could not remove stale webhook {stale['id']}: {e}")

    if existing:
        webhook_id = existing["id"]
        secret = credentials.get("webhook_secret")
        if webhook_id != stored_id or not secret:
            secret = await client.get_webhook_secret(webhook_id)
    else:
        created = await client.create_webhook(target_url, _WEBHOOK_EVENT_TYPES)
        webhook_id = created.get("id")
        if not webhook_id:
            raise _setup_error("Dial did not return an id for the call-status webhook.")
        secret = created.get("secret") or await client.get_webhook_secret(webhook_id)
        logger.info(f"[Dial] created call-status webhook {webhook_id} -> {target_url}")

    # Without a secret every delivery would fail verification, and calls that
    # never connect could not close their runs.
    if not secret:
        raise _setup_error(
            "Dial did not return a signing secret for the call-status webhook."
        )
    return {**credentials, "webhook_id": webhook_id, "webhook_secret": secret}


async def _ensure_dial_setup(
    credentials: Dict[str, Any],
    existing_credentials: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    api_key = credentials.get("api_key")
    if not api_key:
        return credentials

    client = DialClient(api_key)

    try:
        account = await client.get_account()
    except DialAPIError as e:
        if e.status_code in (401, 403):
            raise _setup_error("Dial rejected this API key.", e) from e
        raise _setup_error(f"Could not reach Dial to verify the API key: {e}", e) from e

    account_id = account.get("id")
    if not account_id:
        raise _setup_error("Dial did not return an account id for this API key.")

    # A key for a different Dial account invalidates everything derived from
    # the old one: its signing secret and webhook belong to that account.
    if credentials.get("account_id") != account_id:
        for field in _SERVER_MANAGED_FIELDS:
            credentials.pop(field, None)
    credentials = {**credentials, "account_id": account_id}

    backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()

    try:
        await _require_self_hosted_access(client)
        await client.activate_self_hosted_audio(
            media_socket_base_url(wss_backend_endpoint, account_id), DIAL_AUDIO_FORMAT
        )
        secret = await client.get_self_hosted_secret("audio")
        if not secret:
            raise _setup_error(
                "Dial did not return a Self-Hosted audio signing secret."
            )
        credentials["audio_signing_secret"] = secret
        credentials = await _ensure_webhook(
            client, credentials, events_url(backend_endpoint, account_id)
        )
    except DialAPIError as e:
        raise _setup_error(f"Dial setup failed: {e}", e) from e

    logger.info(f"[Dial] Self-Hosted audio active for account {account_id}")
    return credentials


_UI_METADATA = ProviderUIMetadata(
    display_name="Dial",
    docs_url="https://docs.dograh.com/integrations/telephony/dial",
    fields=[
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="password",
            sensitive=True,
            description=(
                "Dial API key (sk_live_…) from https://getdial.ai/dashboard. "
                "On save, Dograh switches the account's Self-Hosted audio mode "
                "on and points it here, so every call on this Dial account — "
                "inbound and outbound — is handled by Dograh."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Dial phone numbers used for calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="dial",
    provider_cls=DialProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=DIAL_SAMPLE_RATE,
    config_request_cls=DialConfigurationRequest,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="account_id",
    server_managed_credential_fields=_SERVER_MANAGED_FIELDS,
    preprocess_credentials_on_save=_ensure_dial_setup,
    supports_phone_number_sync=True,
)


register(SPEC)


__all__ = [
    "SPEC",
    "DialConfigurationRequest",
    "DialProvider",
    "create_transport",
]
