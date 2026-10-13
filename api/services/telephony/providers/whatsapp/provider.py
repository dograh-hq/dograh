"""WhatsApp implementation of the TelephonyProvider interface.

Inbound only. Meta announces calls on the webhook in ``routes.py`` and media
runs over WebRTC through pipecat's ``WhatsAppClient``, so the parts of the
interface built around outbound dialing or a media websocket have nothing to
do here and say so.
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

OUTBOUND_UNSUPPORTED_DETAIL = (
    "WhatsApp configurations receive calls only; outbound calling is not supported."
)


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


def _first_call(webhook_data: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """The ``(metadata, call)`` of a Meta ``calls`` webhook, or empty dicts."""
    entry = (webhook_data.get("entry") or [{}])[0]
    change = (entry.get("changes") or [{}])[0]
    value = change.get("value") or {}
    call = (value.get("calls") or [{}])[0]
    return value.get("metadata") or {}, call


class WhatsAppProvider(TelephonyProvider):
    """Receive WhatsApp voice calls on a Cloud API business number."""

    PROVIDER_NAME = WorkflowRunMode.WHATSAPP.value
    WEBHOOK_ENDPOINT = "whatsapp"

    def __init__(self, config: Dict[str, Any]):
        self.access_token: str = config.get("access_token") or ""
        self.phone_number_id: str = config.get("phone_number_id") or ""
        self.app_secret: str = config.get("app_secret") or ""
        self.from_numbers = config.get("from_numbers") or []

        for name in ("access_token", "phone_number_id", "app_secret"):
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
    # Credentials and the business number
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        return bool(self.access_token and self.phone_number_id and self.app_secret)

    async def get_available_phone_numbers(self) -> List[str]:
        """The one number this configuration is for, as Meta displays it."""
        url = f"{GRAPH_API_BASE_URL}/{self.phone_number_id}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params={"fields": "display_phone_number"}
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise ProviderPhoneNumberLookupError(
                            f"Meta Graph API {response.status}: {body}",
                            status_code=response.status,
                        )
                    payload = await response.json()
        except ProviderPhoneNumberLookupError:
            raise
        except Exception as e:
            raise ProviderPhoneNumberLookupError(
                f"WhatsApp phone-number lookup failed: {e}"
            ) from e

        display_phone_number = payload.get("display_phone_number")
        if not display_phone_number:
            raise ProviderPhoneNumberLookupError(
                f"Meta returned no display_phone_number for phone number ID "
                f"{self.phone_number_id}"
            )
        return [_canonical(display_phone_number)]

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Accept only the number Meta shows behind the configured Phone Number ID."""
        normalized = _canonical(address)
        if normalized in await self.get_available_phone_numbers():
            return ProviderSyncResult(ok=True)
        return ProviderSyncResult(
            ok=False,
            message=(
                f"{normalized} is not the WhatsApp number behind phone number ID "
                f"{self.phone_number_id}."
            ),
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
        metadata, call = _first_call(webhook_data)
        return NormalizedInboundData(
            provider=WhatsAppProvider.PROVIDER_NAME,
            call_id=call.get("id") or "",
            from_number=_canonical(call.get("from")),
            to_number=_canonical(
                call.get("to") or metadata.get("display_phone_number")
            ),
            direction=call.get("direction") or "inbound",
            call_status=call.get("event") or "unknown",
            account_id=metadata.get("phone_number_id"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return bool(webhook_account_id) and (
            config_data.get("phone_number_id") == webhook_account_id
        )

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        _, call = _first_call(data)
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
