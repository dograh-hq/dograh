"""WhatsApp implementation of the TelephonyProvider interface.

Inbound only. Meta announces calls on the webhook in ``routes.py`` and media
runs over WebRTC through pipecat's ``WhatsAppClient``, so the parts of the
interface built around outbound dialing or a media websocket have nothing to
do here and say so.

A configuration is one WhatsApp Business Account (WABA). Its phone numbers are
read from Meta rather than typed in, and each call names its number by the
``phone_number_id`` Meta puts in the webhook.
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException, Response

from api.enums import WorkflowRunMode
from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderPhoneNumber,
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket

# Pinned to the version pipecat's ``WhatsAppApi`` answers calls on, so every
# Graph API request for a call runs on one version. Bump both together.
GRAPH_API_BASE_URL = "https://graph.facebook.com/v23.0"

# Fields read for each number on the account. ``display_phone_number`` is the
# address; the rest is kept on the row so the UI can show which Meta number a
# row stands for without another round trip.
PHONE_NUMBER_FIELDS = "id,display_phone_number,verified_name,platform_type,account_mode"

# A WABA holds at most a few dozen numbers; this only stops a paging loop
# from running away on a malformed ``next`` link.
MAX_PHONE_NUMBER_PAGES = 20

# ``telephony_phone_numbers.label`` width.
LABEL_MAX_LENGTH = 64

OUTBOUND_UNSUPPORTED_DETAIL = (
    "WhatsApp configurations receive calls only; outbound calling is not supported."
)


class MetaGraphError(Exception):
    """Meta turned a Graph API request down, or could not be reached.

    ``status_code`` is Meta's HTTP status when it answered, ``None`` when the
    request failed before a response.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def signature_matches(
    app_secret: str, raw_body: bytes | str, signature_header: Optional[str]
) -> bool:
    """Check Meta's ``X-Hub-Signature-256`` (``sha256=<hex HMAC of the body>``)."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    body = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # Compared as bytes: ``compare_digest`` raises on a non-ASCII str, and the
    # header is whatever the sender put there.
    received = signature_header[len("sha256=") :].encode("utf-8")
    return hmac.compare_digest(expected.encode("ascii"), received)


def _canonical(address: Optional[str]) -> str:
    return normalize_telephony_address(address).canonical if address else ""


def _first_call(
    webhook_data: Dict[str, Any],
) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    """The ``(waba_id, metadata, call)`` of a Meta ``calls`` webhook, or empties."""
    entry = (webhook_data.get("entry") or [{}])[0]
    change = (entry.get("changes") or [{}])[0]
    value = change.get("value") or {}
    call = (value.get("calls") or [{}])[0]
    return str(entry.get("id") or ""), value.get("metadata") or {}, call


async def _graph_request(
    method: str,
    url: str,
    access_token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One Graph API request with the bearer token; any non-200 is an error."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with aiohttp.ClientSession() as session:
            request = getattr(session, method)
            async with request(url, headers=headers, params=params) as response:
                if response.status != 200:
                    body = await response.text()
                    raise MetaGraphError(
                        f"Meta Graph API {response.status}: {body}",
                        status_code=response.status,
                    )
                payload = await response.json()
    except MetaGraphError:
        raise
    except Exception as e:
        raise MetaGraphError(f"Meta Graph API request failed: {e}") from e
    return payload if isinstance(payload, dict) else {}


class WhatsAppProvider(TelephonyProvider):
    """Receive WhatsApp voice calls on the numbers of one Business Account."""

    PROVIDER_NAME = WorkflowRunMode.WHATSAPP.value
    WEBHOOK_ENDPOINT = "whatsapp"

    def __init__(self, config: Dict[str, Any]):
        self.access_token: str = config.get("access_token") or ""
        self.waba_id: str = config.get("waba_id") or ""
        self.app_secret: str = config.get("app_secret") or ""
        self.from_numbers = config.get("from_numbers") or []

        for name in ("access_token", "waba_id", "app_secret"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required for WhatsApp provider")

    # ------------------------------------------------------------------
    # Outbound: not supported
    # ------------------------------------------------------------------

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        raise HTTPException(status_code=400, detail=OUTBOUND_UNSUPPORTED_DETAIL)

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Meta pushes call state over the webhook; there is nothing to poll."""
        raise NotImplementedError("WhatsApp call state arrives on the webhook")

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """Meta exposes no per-call cost for WhatsApp calling."""
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "raw_response": {"call_id": call_id, "provider": self.PROVIDER_NAME},
        }

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return {
            "call_sid": transfer_id,
            "status": "failed",
            "provider": self.PROVIDER_NAME,
            "message": "WhatsApp call transfer is not supported",
        }

    def supports_transfers(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Credentials and the account's numbers
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        return bool(self.access_token and self.waba_id and self.app_secret)

    async def list_provider_phone_numbers(self) -> List[ProviderPhoneNumber]:
        """Every number on the Business Account, as Meta lists it.

        Also the credential check at save time: a token that cannot read the
        account's numbers cannot answer its calls either.
        """
        url: str = f"{GRAPH_API_BASE_URL}/{self.waba_id}/phone_numbers"
        params: Optional[Dict[str, Any]] = {"fields": PHONE_NUMBER_FIELDS}
        numbers: List[ProviderPhoneNumber] = []
        visited: set[str] = set()
        # An incomplete listing must never pass for a complete one: the sync
        # reports every row it did not see as gone from the account.
        for _ in range(MAX_PHONE_NUMBER_PAGES):
            visited.add(url)
            try:
                payload = await _graph_request(
                    "get", url, self.access_token, params=params
                )
            except MetaGraphError as e:
                raise ProviderPhoneNumberLookupError(
                    f"WhatsApp phone-number listing failed: {e}",
                    status_code=e.status_code,
                ) from e
            data = payload.get("data")
            if not isinstance(data, list):
                raise ProviderPhoneNumberLookupError(
                    "Meta answered the phone-number listing without a data list"
                )
            for item in data:
                number = self._provider_phone_number(item)
                if number is not None:
                    numbers.append(number)
            # Meta's ``next`` link already carries the query, so it is
            # followed as-is.
            paging = payload.get("paging")
            next_url = paging.get("next") if isinstance(paging, dict) else None
            if paging is not None and not isinstance(paging, dict):
                raise ProviderPhoneNumberLookupError(
                    "Meta answered the phone-number listing with invalid paging"
                )
            if next_url is not None and not isinstance(next_url, str):
                raise ProviderPhoneNumberLookupError(
                    "Meta answered the phone-number listing with an invalid next link"
                )
            url = next_url or ""
            params = None
            if not url:
                return numbers
            if url in visited:
                raise ProviderPhoneNumberLookupError(
                    "Meta's phone-number listing repeated a page"
                )
        raise ProviderPhoneNumberLookupError(
            f"Meta's phone-number listing ran past {MAX_PHONE_NUMBER_PAGES} pages"
        )

    @staticmethod
    def _provider_phone_number(item: Dict[str, Any]) -> Optional[ProviderPhoneNumber]:
        address = _canonical(item.get("display_phone_number"))
        if not address:
            return None
        verified_name = item.get("verified_name") or None
        metadata = {
            "phone_number_id": item.get("id"),
            "verified_name": verified_name,
            "platform_type": item.get("platform_type"),
            "account_mode": item.get("account_mode"),
        }
        return ProviderPhoneNumber(
            address=address,
            external_id=str(item.get("id") or "") or None,
            label=verified_name[:LABEL_MAX_LENGTH] if verified_name else None,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )

    async def get_available_phone_numbers(self) -> List[str]:
        """The account's numbers, canonical, straight from Meta."""
        return [n.address for n in await self.list_provider_phone_numbers()]

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Accept only a number Meta lists on the configured Business Account."""
        normalized = _canonical(address)
        if normalized in await self.get_available_phone_numbers():
            return ProviderSyncResult(ok=True)
        return ProviderSyncResult(
            ok=False,
            message=(
                f"{normalized} is not a number on WhatsApp Business Account "
                f"{self.waba_id}."
            ),
        )

    async def subscribe_app(self) -> None:
        """Subscribe the token's Meta app to this Business Account's webhooks.

        Without this Meta never delivers the account's ``calls`` events to the
        app, however the app's webhook is configured. Idempotent on Meta's
        side, so it is safe to repeat on every save.
        """
        url = f"{GRAPH_API_BASE_URL}/{self.waba_id}/subscribed_apps"
        payload = await _graph_request("post", url, self.access_token)
        if not payload.get("success"):
            raise MetaGraphError(
                f"Meta did not confirm the app subscription: {json.dumps(payload)}"
            )

    # ------------------------------------------------------------------
    # Webhook signatures
    # ------------------------------------------------------------------

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        raw_body = params.get("_raw_body", "") if isinstance(params, dict) else ""
        return signature_matches(self.app_secret, raw_body, signature)

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        signature = next(
            (v for k, v in headers.items() if k.lower() == "x-hub-signature-256"),
            None,
        )
        return signature_matches(self.app_secret, body, signature)

    # ------------------------------------------------------------------
    # Shared inbound dispatcher hooks. Meta's webhook is served by routes.py,
    # not /inbound/run, so these only satisfy the interface.
    # ------------------------------------------------------------------

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        return webhook_data.get("object") == "whatsapp_business_account"

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        waba_id, metadata, call = _first_call(webhook_data)
        return NormalizedInboundData(
            provider=WhatsAppProvider.PROVIDER_NAME,
            call_id=call.get("id") or "",
            from_number=_canonical(call.get("from")),
            to_number=_canonical(
                call.get("to") or metadata.get("display_phone_number")
            ),
            direction=call.get("direction") or "inbound",
            call_status=call.get("event") or "unknown",
            # The Business Account is the routing key; the number is below it.
            account_id=waba_id or None,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return bool(webhook_account_id) and (
            config_data.get("waba_id") == webhook_account_id
        )

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        _, _, call = _first_call(data)
        return {
            "call_id": call.get("id") or "",
            "status": (call.get("status") or call.get("event") or "unknown").lower(),
            "from_number": call.get("from") or "",
            "to_number": call.get("to") or "",
            "duration": call.get("duration"),
            "extra": data,
        }

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        """Media is negotiated in routes.py from the webhook's SDP offer."""
        return Response(status_code=204)

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        """Meta is answered with an SDP, not call-control markup."""
        return ""

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        raise NotImplementedError("WhatsApp calls do not use a media websocket")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> Any:
        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> Any:
        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )
        return Response(
            content=json.dumps(
                {
                    "error": getattr(error_type, "value", str(error_type)),
                    "message": message,
                }
            ),
            media_type="application/json",
        )
