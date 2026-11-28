"""Dial implementation of the TelephonyProvider interface.

Dial (https://getdial.ai) is a phone-number API for AI agents. Its
Self-Hosted audio mode streams a call's raw audio, full duplex, to a
WebSocket server the customer runs — here, Dograh. Two things set it apart
from the markup-response carriers:

* Dial opens the media socket itself, to one account-wide URL with the call
  id appended. There is no per-call answer URL, so ``get_webhook_response``
  and ``start_inbound_stream`` are never on the path; ``routes.py`` receives
  the socket and resolves (or, for inbound, creates) the workflow run.
* Inbound calls never reach ``/inbound/run``: they arrive on that same socket,
  announced by its first ``call_connected`` frame.
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import HTTPException
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderPhoneNumber,
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.telephony_address import normalize_telephony_address

from .client import DialAPIError, DialClient
from .signatures import SIGNATURE_HEADER, verify_dial_signature

if TYPE_CHECKING:
    from fastapi import WebSocket

PROVIDER_NAME = WorkflowRunMode.DIAL.value

# POST /api/v1/calls requires an instruction. In Self-Hosted audio mode Dial
# runs no agent of its own; the text only comes back to us in call_connected.
_OUTBOUND_INSTRUCTION = "Conversation handled by a Dograh voice agent."


def _can_call(number: Dict[str, Any]) -> bool:
    capabilities = number.get("capabilities") or []
    return "call" in capabilities and number.get("callingEnabled", True) is not False


class DialProvider(TelephonyProvider):
    """Places and receives calls through Dial's Self-Hosted audio mode."""

    PROVIDER_NAME = PROVIDER_NAME
    # Never fetched: Dial dials our media socket directly. Set so the shared
    # initiate-call route can still format the (unused) webhook_url argument.
    WEBHOOK_ENDPOINT = "dial/unused"

    def __init__(self, config: Dict[str, Any]):
        self.api_key: Optional[str] = config.get("api_key")
        self.account_id: Optional[str] = config.get("account_id")
        self.audio_signing_secret: Optional[str] = config.get("audio_signing_secret")
        self.webhook_secret: Optional[str] = config.get("webhook_secret")
        self.from_numbers = config.get("from_numbers", []) or []
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]
        self.default_from_number = config.get("default_from_number")

    def _client(self) -> DialClient:
        if not self.api_key:
            raise ValueError("Dial provider not properly configured: missing API key")
        return DialClient(self.api_key)

    # ======== OUTBOUND ========

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Place a call via ``POST /api/v1/calls``.

        Dial connects the answered call's audio to the Self-Hosted URL that
        was set on save; ``routes.py`` matches it back to this run through
        the returned call id, which the caller stores in ``gathered_context``.
        """
        if not self.validate_config():
            raise ValueError("Dial provider not properly configured")

        from_number = self.select_from_number(from_number)
        body = {
            "to": to_number,
            "fromNumber": from_number,
            "outboundInstruction": _OUTBOUND_INSTRUCTION,
        }
        # A retried request for the same run must not ring the callee twice.
        idempotency_key = (
            f"dograh-run-{workflow_run_id}" if workflow_run_id is not None else None
        )

        try:
            call = await self._client().make_call(body, idempotency_key=idempotency_key)
        except DialAPIError as e:
            raise HTTPException(status_code=e.status_code or 502, detail=str(e)) from e

        call_id = call.get("id")
        if not call_id:
            raise HTTPException(status_code=502, detail="Dial did not return a call id")

        logger.info(f"Dial call {call_id} placed for workflow run {workflow_run_id}")
        return CallInitiationResult(
            call_id=call_id,
            status=str(call.get("status") or "queued"),
            caller_number=call.get("from") or from_number,
            provider_metadata={"call_id": call_id},
            raw_response=call,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        return await self._client().get_call(call_id)

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(self.api_key and self.from_numbers)

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """Dial bills from the account's credit wallet and exposes no per-call price."""
        try:
            call = await self._client().get_call(call_id)
        except (DialAPIError, ValueError) as e:
            logger.warning(f"Could not fetch Dial call {call_id}: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}
        return {
            "cost_usd": 0.0,
            "duration": int(call.get("duration") or 0),
            "status": call.get("status", "unknown"),
            "raw_response": call,
        }

    # ======== STATUS WEBHOOKS ========

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """Dial signs the raw body; ``params`` carries it under ``raw_body``."""
        return verify_dial_signature(
            self.webhook_secret, signature, str(params.get("raw_body", ""))
        )

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Map a ``call.ended`` / ``call.status_changed`` event to the generic shape.

        https://docs.getdial.ai/api-reference/events/call-ended
        https://docs.getdial.ai/api-reference/events/call-status-changed
        """
        payload = data.get("data") or {}
        event_type = data.get("type")

        status: Any
        if event_type == "call.ended":
            status = payload.get("status")
        else:
            state = (payload.get("status") or {}).get("state")
            status = {
                "Queued": TelephonyCallStatus.INITIATED.value,
                "Ringing": TelephonyCallStatus.RINGING.value,
                "In-Progress": TelephonyCallStatus.IN_PROGRESS.value,
                "Terminated": payload.get("terminationType"),
            }.get(state, state)

        duration = payload.get("durationSeconds")
        return {
            "call_id": payload.get("callId") or "",
            "status": TelephonyCallStatus.from_raw(status) or str(status or ""),
            "from_number": payload.get("from"),
            "to_number": payload.get("to"),
            "direction": payload.get("direction"),
            "duration": str(duration) if duration is not None else None,
            "extra": data,
        }

    # ======== MEDIA SOCKET ========

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        raise NotImplementedError(
            "Dial connects call audio to the Self-Hosted URL directly; "
            "there is no answer-URL step"
        )

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """Dial never dials the generic ``/ws/...`` endpoint.

        Its socket lands on ``/telephony/dial/ws/{account_id}/{call_id}``
        (see ``routes.py``), so reaching this means a misrouted connection.
        """
        logger.error(
            f"Dial media socket reached the generic endpoint for run {workflow_run_id}"
        )
        await websocket.close(code=4400, reason="Dial uses its own media endpoint")

    # ======== INBOUND ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        # Inbound Dial calls arrive on the media socket, never on /inbound/run.
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Normalize a Self-Hosted audio ``call_connected`` frame.

        https://docs.getdial.ai/api-reference/self-hosted-audio-protocol/call-connected
        """
        from_raw = webhook_data.get("from") or ""
        to_raw = webhook_data.get("to") or ""
        return NormalizedInboundData(
            provider=PROVIDER_NAME,
            call_id=webhook_data.get("call_id") or "",
            from_number=(
                normalize_telephony_address(from_raw).canonical if from_raw else ""
            ),
            to_number=normalize_telephony_address(to_raw).canonical if to_raw else "",
            direction=webhook_data.get("direction") or "",
            call_status="in-progress",
            account_id=webhook_data.get("account_id"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if not webhook_account_id:
            return False
        return config_data.get("account_id") == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """Verify a Dial webhook delivery (HMAC over ``<t>.<raw body>``)."""
        signature = {k.lower(): v for k, v in headers.items()}.get(SIGNATURE_HEADER)
        if not signature:
            logger.warning("Dial webhook delivery missing X-Dial-Signature")
            return False
        return verify_dial_signature(self.webhook_secret, signature, body)

    def verify_media_socket_signature(
        self, call_id: str, header: Optional[str]
    ) -> bool:
        """Verify the media socket's signature (HMAC over ``<t>.<call_id>``)."""
        return verify_dial_signature(self.audio_signing_secret, header, call_id)

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        raise NotImplementedError(
            "Dial delivers inbound calls on its own media socket, not /inbound/run"
        )

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Check the number is on this Dial account and can place calls."""
        normalized = normalize_telephony_address(address)
        if normalized.address_type != "pstn":
            return ProviderSyncResult(
                ok=False, message="Dial numbers are E.164 phone numbers"
            )

        numbers = await self._list_numbers_or_raise()
        match = next(
            (n for n in numbers if n.get("number") == normalized.canonical), None
        )
        if not match:
            return ProviderSyncResult(
                ok=False,
                message=(
                    f"Phone number {normalized.canonical} is not on this Dial "
                    "account. Add it at https://getdial.ai/dashboard first."
                ),
            )
        if not _can_call(match):
            return ProviderSyncResult(
                ok=False,
                message=(
                    f"Dial number {normalized.canonical} cannot place calls "
                    "(it has no voice capability, or calling is switched off)."
                ),
            )
        return ProviderSyncResult(ok=True)

    async def list_provider_phone_numbers(self) -> List[ProviderPhoneNumber]:
        numbers = await self._list_numbers_or_raise()
        return [
            ProviderPhoneNumber(
                address=normalize_telephony_address(n["number"]).canonical,
                external_id=n.get("id"),
                label=n.get("nickname"),
                metadata={"capabilities": n.get("capabilities") or []},
            )
            for n in numbers
            if n.get("number") and _can_call(n)
        ]

    async def _list_numbers_or_raise(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            raise ProviderPhoneNumberLookupError(
                "A Dial API key is required to look up phone numbers"
            )
        try:
            return await DialClient(self.api_key).list_numbers()
        except DialAPIError as e:
            raise ProviderPhoneNumberLookupError(
                str(e), status_code=e.status_code
            ) from e

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi import Response

        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )
        return Response(
            content=json.dumps({"error": str(error_type), "message": message}),
            media_type="application/json",
        )

    # ======== TRANSFERS ========

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Dial does not support mid-call transfers yet")

    def supports_transfers(self) -> bool:
        return False
