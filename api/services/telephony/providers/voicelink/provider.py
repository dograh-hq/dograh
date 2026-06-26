"""
VoiceLink implementation of the TelephonyProvider interface.

VoiceLink is an Indian reseller telephony platform. Outbound dials go
through ``POST /v1/add_lead``; VoiceLink then connects back to our
WebSocket endpoint for media (Twilio-media-streams-style JSON events,
G.711 A-law 8 kHz) and POSTs nested-JSON call-lifecycle events to our
webhook route.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import aiohttp
from fastapi import HTTPException, WebSocketDisconnect
from loguru import logger

from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket


def normalize_customer_number(raw: str) -> str:
    """Normalize a destination number to the bare 10-digit local form.

    VoiceLink's carrier requires ``customer_number`` to be the bare local
    number — a 91-prefixed 12-digit number fails at the carrier with Q.850
    cause 38 ("Network out of order"). Strips all non-digits, then:

    - 12 digits starting with "91" → drop the country code
    - 11 digits starting with "0" → drop the trunk prefix

    Args:
        raw: Destination number in any common form
            (e.g. "+91 73404 00524", "917340400524", "07340400524").

    Returns:
        The bare local number (e.g. "7340400524").
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


# VoiceLink webhook event → normalized status understood by
# api.services.telephony.status_processor._process_status_update.
_EVENT_STATUS = {
    "call.initiated": "initiated",
    "call.ringing": "ringing",
    "call.answered": "in-progress",
    "call.completed": "completed",
    "call.ended": "completed",
    "call.failed": "failed",
}


class VoiceLinkProvider(TelephonyProvider):
    """
    VoiceLink implementation of TelephonyProvider.

    Call-control style: ``add_lead`` queues the dial and carries our
    ``websocket_url`` (media) and ``webhook_url`` (call events) inline, so
    there is no markup/answer-URL step.
    """

    PROVIDER_NAME = WorkflowRunMode.VOICELINK.value
    WEBHOOK_ENDPOINT = "voicelink/events"

    DEFAULT_API_BASE = "https://app.voicelink.co.in/api"

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize VoiceLinkProvider with configuration.

        Args:
            config: Dictionary containing:
                - api_base: VoiceLink API base URL
                - username / password: login credentials (enable token refresh)
                - bearer_token: optional static bearer token
                - did_number: registered DID used as caller id (e.g. 919484959244)
                - from_numbers: list of DID addresses attached to the config
        """
        self.api_base = (config.get("api_base") or self.DEFAULT_API_BASE).rstrip("/")
        self.username = config.get("username")
        self.password = config.get("password")
        self.bearer_token = config.get("bearer_token")
        self.did_number = config.get("did_number")
        self.from_numbers = config.get("from_numbers", [])

        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self._access_token: Optional[str] = self.bearer_token or None

    # ======== AUTH / HTTP HELPERS ========

    async def _login(self) -> str:
        """Obtain a bearer token via /v1/auth/login. Never logs the password."""
        if not self.username or not self.password:
            raise HTTPException(
                status_code=401,
                detail=(
                    "VoiceLink token rejected and no username/password "
                    "configured for re-login"
                ),
            )

        endpoint = f"{self.api_base}/v1/auth/login"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                json={"username": self.username, "password": self.password},
            ) as response:
                body = await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"VoiceLink login failed for user {self.username}: "
                        f"HTTP {response.status}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"VoiceLink login failed: HTTP {response.status}",
                    )
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    raise HTTPException(
                        status_code=502,
                        detail="VoiceLink login returned a non-JSON response",
                    )

        token = (data.get("data") or {}).get("access_token")
        if not token:
            raise HTTPException(
                status_code=502,
                detail="VoiceLink login response missing data.access_token",
            )

        logger.info(f"VoiceLink login succeeded for user {self.username}")
        self._access_token = token
        return token

    async def _send_request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        token: str,
    ) -> Tuple[int, Any]:
        """Single HTTP exchange against the VoiceLink API."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, json=payload, headers=headers
            ) as response:
                body = await response.text()
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    data = {"raw": body}
                return response.status, data

    async def _api_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any]:
        """Authenticated VoiceLink API request with one 401 re-login retry."""
        token = self._access_token or await self._login()
        url = f"{self.api_base}{path}"

        status, data = await self._send_request(method, url, payload, token)
        if status == 401 and self.username and self.password:
            logger.info("VoiceLink token rejected (401); re-logging in and retrying")
            token = await self._login()
            status, data = await self._send_request(method, url, payload, token)
        return status, data

    # ======== OUTBOUND CALL ========

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via VoiceLink ``POST /v1/add_lead``.

        VoiceLink differences from REST providers like Twilio:
        - ``customer_number`` must be the bare 10-digit local number (a
          91-prefixed number fails at the carrier with Q.850 cause 38).
        - ``did_number`` keeps its registered form (e.g. "919484959244").
        - The response carries an ``outbound_queue_id`` only — the real call
          id (uuid) arrives later via webhook events and the WS start event.
        - ``websocket_url``/``webhook_url`` are passed inline per call, so
          the answer-URL style ``webhook_url`` argument is unused.
        """
        if not self.validate_config():
            raise ValueError("VoiceLink provider not properly configured")

        workflow_id = kwargs.get("workflow_id")
        user_id = kwargs.get("user_id")
        if workflow_id is None or user_id is None or workflow_run_id is None:
            raise ValueError(
                "VoiceLink initiate_call requires workflow_id, user_id and "
                "workflow_run_id to build the media WebSocket URL"
            )

        customer_number = normalize_customer_number(to_number)
        if len(customer_number) != 10:
            logger.warning(
                f"VoiceLink customer_number normalized to "
                f"{len(customer_number)} digits (expected 10): "
                f"{customer_number!r} from {to_number!r}"
            )

        # DID keeps its registered (91-prefixed) form; only strip formatting.
        did_source = from_number or self.did_number or (
            self.from_numbers[0] if self.from_numbers else ""
        )
        did_number = re.sub(r"\D", "", did_source)
        logger.info(f"Selected VoiceLink DID {did_number} for outbound call")

        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()

        websocket_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws"
            f"/{workflow_id}/{user_id}/{workflow_run_id}"
        )
        events_url = (
            f"{backend_endpoint}/api/v1/telephony/voicelink/events/{workflow_run_id}"
        )

        payload = {
            "did_number": did_number,
            "customer_number": customer_number,
            "custom_parameters": json.dumps(
                {
                    "workflow_id": workflow_id,
                    "user_id": user_id,
                    "workflow_run_id": workflow_run_id,
                }
            ),
            "websocket_url": websocket_url,
            "webhook_url": events_url,
        }

        logger.info(
            f"VoiceLink add_lead payload: did_number={did_number}, "
            f"customer_number={customer_number}, websocket_url={websocket_url}, "
            f"webhook_url={events_url}"
        )

        status, data = await self._api_request("POST", "/v1/add_lead", payload)

        if status not in (200, 201) or not isinstance(data, dict) or not data.get(
            "status"
        ):
            logger.error(f"VoiceLink add_lead failed: HTTP {status} body={data}")
            raise HTTPException(
                status_code=status if status >= 400 else 502,
                detail=f"Failed to initiate VoiceLink call: HTTP {status} {data}",
            )

        lead = data.get("data") or {}
        outbound_queue_id = lead.get("outbound_queue_id")

        # No call id exists at originate time — the provider call id (uuid)
        # arrives via webhook events / the WS start event. Use the queue id
        # as the provisional identifier.
        call_id = (
            str(outbound_queue_id)
            if outbound_queue_id is not None
            else f"voicelink-run-{workflow_run_id}"
        )

        logger.info(
            f"VoiceLink call queued successfully. outbound_queue_id={outbound_queue_id}"
        )

        return CallInitiationResult(
            call_id=call_id,
            status="queued",
            caller_number=did_number,
            provider_metadata={
                "outbound_queue_id": outbound_queue_id,
                "bot_id": lead.get("bot_id"),
                "client_id": lead.get("client_id"),
                "carrier_id": lead.get("carrier_id"),
            },
            raw_response=data,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        VoiceLink does not expose a per-call status REST endpoint; status
        arrives via webhook events on ``webhook_url``.
        """
        return {"call_id": call_id, "status": "unknown"}

    async def get_available_phone_numbers(self) -> List[str]:
        """Get list of available VoiceLink DID numbers."""
        if self.from_numbers:
            return self.from_numbers
        return [self.did_number] if self.did_number else []

    def validate_config(self) -> bool:
        """Validate VoiceLink configuration."""
        has_auth = bool(self.bearer_token or (self.username and self.password))
        return bool(self.api_base and self.did_number and has_auth)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """VoiceLink webhooks are unsigned — no verification possible."""
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """Not used for VoiceLink — the media WebSocket URL is passed inline
        with the ``add_lead`` request."""
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """VoiceLink does not expose per-call cost via API."""
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "raw_response": {},
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse a VoiceLink call-event webhook into the generic format.

        VoiceLink POSTs nested camelCase JSON::

            {"event": "call.answered", "timestamp": ..., "call": {
                "id": "<uuid>", "direction": "outbound", "from": ..., "to": ...,
                "status": ..., "hangupCause": "16", "durationSec": 42, ...}}
        """
        call = data.get("call") or {}
        event = (data.get("event") or "").lower()
        status = _EVENT_STATUS.get(event, event)

        duration = call.get("durationSec")
        # Field name unconfirmed upstream — check both spellings defensively.
        recording_url = call.get("recordingUrl") or call.get("recording_url")

        extra = dict(data)
        if recording_url:
            extra["recording_url"] = recording_url

        return {
            "call_id": call.get("id", ""),
            "status": status,
            "from_number": call.get("from"),
            "to_number": call.get("to"),
            "direction": call.get("direction"),
            "duration": str(duration) if duration is not None else None,
            "extra": extra,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle the VoiceLink media WebSocket connection.

        VoiceLink connects to our ``websocket_url`` and sends:
        1. ``connected`` event on WebSocket open
        2. ``start`` event with stream_sid, call_sid, custom_parameters and
           media_format (audio/alaw @ 8000)
        3. ``media`` events with base64 A-law audio
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            first_msg = json.loads(await websocket.receive_text())
            logger.debug(f"Received the first message: {first_msg}")

            if first_msg.get("event") == "connected":
                start_msg = json.loads(await websocket.receive_text())
            else:
                start_msg = first_msg

            if start_msg.get("event") != "start":
                logger.error(
                    f"Expected 'start' event, got: {start_msg.get('event')}"
                )
                await websocket.close(code=4400, reason="Expected start event")
                return

            start_data = start_msg.get("start", {}) or {}
            stream_sid = (
                start_data.get("stream_sid")
                or start_data.get("streamSid")
                or start_msg.get("stream_sid")
                or ""
            )
            call_sid = (
                start_data.get("call_sid") or start_data.get("callSid") or ""
            )

            if not stream_sid:
                logger.error(f"Missing stream_sid in start event: {start_data}")
                await websocket.close(code=4400, reason="Missing stream_sid")
                return

            logger.info(
                f"[run {workflow_run_id}] VoiceLink stream started - "
                f"stream_sid={stream_sid}, call_sid={call_sid}"
            )

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
                call_id=call_sid or stream_sid,
                transport_kwargs={"stream_id": stream_sid, "call_id": call_sid},
            )

            logger.info(f"[run {workflow_run_id}] VoiceLink pipeline completed")

        except WebSocketDisconnect as e:
            # VoiceLink may close the socket before sending start if the call
            # never connects — surface as an expected end-of-call.
            logger.info(
                f"[run {workflow_run_id}] VoiceLink WebSocket closed before "
                f"stream start: code={e.code}, reason={e.reason!r}"
            )
        except Exception as e:
            logger.error(
                f"[run {workflow_run_id}] Error in VoiceLink WebSocket handler: {e}"
            )
            raise

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """Detect a VoiceLink *inbound* call webhook on the generic dispatcher.

        VoiceLink posts nested JSON ``{"event": ..., "call": {...}}``. We only
        claim the webhook when it is an inbound-direction call event, so the
        per-run *outbound* event webhooks (which target
        ``/voicelink/events/{run_id}``) are never accidentally matched here.
        """
        call = webhook_data.get("call")
        if not isinstance(call, dict):
            return False
        if str(call.get("direction", "")).lower() != "inbound":
            return False
        # Require the VoiceLink-shaped envelope so we don't shadow other
        # providers' form posts.
        return "event" in webhook_data or "id" in call

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Parse a VoiceLink event into normalized inbound format.

        Inbound calls are not currently routed through VoiceLink, but the
        nested ``call`` payload is normalized defensively. VoiceLink is
        India-only — hardcode "IN" so local-form numbers normalize to the
        right E.164.
        """
        country = "IN"
        call = webhook_data.get("call") or {}
        from_raw = call.get("from", "")
        to_raw = call.get("to", "")
        return NormalizedInboundData(
            provider=VoiceLinkProvider.PROVIDER_NAME,
            call_id=call.get("id", ""),
            from_number=normalize_telephony_address(
                from_raw, country_hint=country
            ).canonical
            if from_raw
            else "",
            to_number=normalize_telephony_address(
                to_raw, country_hint=country
            ).canonical
            if to_raw
            else "",
            direction="inbound",
            call_status=call.get("status", ""),
            account_id=None,
            from_country=country,
            to_country=country,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """VoiceLink inbound webhooks carry no account id; the DID match is the
        authorization boundary."""
        return True

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """VoiceLink webhooks are unsigned — nothing to verify."""
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        """Answer a VoiceLink inbound call by returning the media WebSocket URL.

        VoiceLink has no markup language. For inbound it expects a JSON body
        telling it where to stream media (mirroring how add_lead carries
        ``websocket_url`` inline for outbound). We also hand it the per-run
        events URL so call-lifecycle events land on the same
        ``/voicelink/events`` route used for outbound.

        NOTE: the exact response keys VoiceLink expects for an inbound answer
        are unconfirmed upstream — this mirrors add_lead's field names and is
        the best-available reference. Adjust after capturing a real inbound
        call (see provider docstring / deploy notes).
        """
        from fastapi import Response

        events_url = (
            f"{backend_endpoint}/api/v1/telephony/voicelink/events/{workflow_run_id}"
        )
        body = {
            "status": "ok",
            "action": "stream",
            "websocket_url": websocket_url,
            "webhook_url": events_url,
            "media_format": {"encoding": "audio/alaw", "sample_rate": 8000},
        }
        logger.info(
            f"[run {workflow_run_id}] VoiceLink inbound answer: "
            f"websocket_url={websocket_url} webhook_url={events_url}"
        )
        return Response(content=json.dumps(body), media_type="application/json")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """Generate a VoiceLink-specific error response (JSON — VoiceLink has
        no markup language)."""
        from fastapi import Response

        return Response(
            content=json.dumps({"status": "error", "message": message}),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        """Generate a VoiceLink-specific validation error response."""
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        return Response(
            content=json.dumps({"status": "error", "message": message}),
            media_type="application/json",
        )

    # ======== CALL TRANSFER METHODS ========

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        VoiceLink call transfers are not implemented yet.

        VoiceLink supports a native ``transfer`` WebSocket event
        (``{"event": "transfer", "target": <number>}``) which can back a
        future implementation.

        Raises:
            NotImplementedError: VoiceLink call transfers are yet to be
                implemented.
        """
        raise NotImplementedError("VoiceLink provider does not support call transfers")

    def supports_transfers(self) -> bool:
        """
        VoiceLink does not support call transfers yet.

        Returns:
            False - VoiceLink provider does not support call transfers
        """
        return False
