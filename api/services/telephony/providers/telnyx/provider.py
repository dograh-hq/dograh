"""
Telnyx implementation of the TelephonyProvider interface.
Uses the Telnyx Call Control API v2 for outbound calling with
inline WebSocket media streaming.
"""

import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException, WebSocketDisconnect
from loguru import logger

from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket


def normalize_event_type(event_type: str) -> str:
    """Telnyx delivers event types with either dots or underscores
    (e.g. ``streaming.started`` vs ``streaming_started``). Normalize to the
    dotted form so all downstream matching can use a single canonical shape.
    """
    return (event_type or "").replace("_", ".")


class TelnyxProvider(TelephonyProvider):
    """
    Telnyx implementation of TelephonyProvider.
    Uses the Call Control API v2 with inline WebSocket streaming for audio.
    """

    PROVIDER_NAME = WorkflowRunMode.TELNYX.value
    WEBHOOK_ENDPOINT = "telnyx/webhook"

    TELNYX_API_BASE = "https://api.telnyx.com/v2"

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key")
        self.connection_id = config.get("connection_id")
        self.webhook_public_key = config.get("webhook_public_key")
        self.from_numbers = config.get("from_numbers", [])

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Initiate an outbound call via Telnyx Call Control API."""
        if not self.validate_config():
            raise ValueError("Telnyx provider not properly configured")

        if from_number is None:
            from_number = random.choice(self.from_numbers)
        logger.info(f"Selected phone number {from_number} for outbound call")

        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()

        # Build the WebSocket stream URL for inline audio streaming
        workflow_id = kwargs.get("workflow_id")
        user_id = kwargs.get("user_id")
        stream_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws"
            f"/{workflow_id}/{user_id}/{workflow_run_id}"
        )

        # Build the webhook URL for status callbacks
        events_url = (
            f"{backend_endpoint}/api/v1/telephony/telnyx/events/{workflow_run_id}"
        )

        # stream_bidirectional_codec controls only the Dograh → Telnyx direction.
        # The Telnyx → Dograh direction follows the PSTN leg and is announced via
        # media_format.encoding in the WebSocket start message.
        payload = {
            "connection_id": self.connection_id,
            "to": to_number,
            "from": from_number,
            "stream_url": stream_url,
            "stream_track": "inbound_track",
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "PCMU",
            "webhook_url": events_url,
            "webhook_url_method": "POST",
        }

        logger.info(
            f"Telnyx dial payload: {json.dumps({k: v for k, v in payload.items() if k != 'connection_id'})}"
        )

        endpoint = f"{self.TELNYX_API_BASE}/calls"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, json=payload, headers=self._headers()
            ) as response:
                if response.status != 200:
                    error_data = await response.json()
                    logger.error(f"Telnyx API error: {error_data}")
                    raise HTTPException(
                        status_code=response.status, detail=json.dumps(error_data)
                    )

                response_data = await response.json()
                data = response_data.get("data", {})
                call_control_id = data.get("call_control_id", "")
                call_leg_id = data.get("call_leg_id", "")
                call_session_id = data.get("call_session_id", "")

                logger.info(
                    f"Telnyx call initiated: call_control_id={call_control_id}, "
                    f"call_leg_id={call_leg_id}, call_session_id={call_session_id}"
                )

                return CallInitiationResult(
                    call_id=call_control_id,
                    status="initiated",
                    caller_number=from_number,
                    provider_metadata={
                        "call_id": call_control_id,
                        "call_control_id": call_control_id,
                        "call_leg_id": call_leg_id,
                        "call_session_id": call_session_id,
                    },
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Get the current status of a Telnyx call."""
        endpoint = f"{self.TELNYX_API_BASE}/calls/{call_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=self._headers()) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(f"Failed to get call status: {error_data}")
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(self.api_key and self.connection_id and self.from_numbers)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """Required by the abstract interface but not actively called for Telnyx.

        Telnyx webhook signature verification uses Ed25519 (via the
        telnyx-signature-ed25519 header). This can be implemented in the
        future using the Telnyx SDK if needed.
        """
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """Not used for Telnyx — streaming is inline with the dial request."""
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """Get cost information for a Telnyx call.

        Telnyx doesn't provide per-call cost via the Call Control API.
        Cost data is available through the billing/CDR APIs.
        """
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "raw_response": {},
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Telnyx webhook event data into generic format."""
        event_data = data.get("data", data)
        event_type = normalize_event_type(event_data.get("event_type", ""))
        payload = event_data.get("payload", {})

        status = self._resolve_status(event_type, payload)

        duration_secs = payload.get("duration_secs")
        return {
            "call_id": payload.get("call_control_id", ""),
            "status": status,
            "from_number": payload.get("from"),
            "to_number": payload.get("to"),
            "direction": payload.get("direction"),
            "duration": str(duration_secs) if duration_secs else None,
            "extra": data,
        }

    @staticmethod
    def _resolve_status(event_type: str, payload: Dict[str, Any]) -> str:
        """Map a Telnyx event type (and hangup cause) to a normalized status."""
        EVENT_STATUS = {
            "call.initiated": "initiated",
            "call.answered": "in-progress",
            "call.hangup": "completed",
            "call.machine.detection.ended": "machine-detected",
            "streaming.started": "streaming-started",
            "streaming.stopped": "streaming-stopped",
        }

        HANGUP_STATUS = {
            "busy": "busy",
            "no_answer": "no-answer",
            "timeout": "no-answer",
            "call_rejected": "failed",
            "unallocated_number": "failed",
        }

        status = EVENT_STATUS.get(event_type, event_type)

        if event_type == "call.hangup":
            hangup_cause = payload.get("hangup_cause", "")
            status = HANGUP_STATUS.get(hangup_cause, status)

        return status

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """Handle Telnyx WebSocket connection for real-time audio.

        Telnyx sends:
        1. "connected" event on WebSocket open
        2. "start" event with stream_id, call_control_id, media_format
        3. "media" events with base64-encoded audio
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            # Wait for "connected" event
            first_msg = await websocket.receive_text()
            msg = json.loads(first_msg)

            if msg.get("event") != "connected":
                logger.error(f"Expected 'connected' event, got: {msg.get('event')}")
                await websocket.close(code=4400, reason="Expected connected event")
                return

            logger.debug(
                f"Telnyx WebSocket connected for workflow_run {workflow_run_id}"
            )

            # Wait for "start" event with stream details
            start_msg = await websocket.receive_text()
            logger.debug(f"Received start message: {start_msg}")

            start_data = json.loads(start_msg)
            if start_data.get("event") != "start":
                logger.error("Expected 'start' event second")
                await websocket.close(code=4400, reason="Expected start event")
                return

            # media_format.encoding is the codec Telnyx delivers on the
            # inbound direction (Telnyx → Dograh); the outbound direction is
            # pinned to PCMU separately via stream_bidirectional_codec.
            try:
                stream_id = start_data.get("stream_id", "")
                start_info = start_data.get("start", {})
                call_control_id = start_info.get("call_control_id", "")
                media_format = start_info.get("media_format") or {}
                encoding = media_format.get("encoding") or "PCMU"
            except (KeyError, AttributeError):
                logger.error("Missing stream_id or call_control_id in start message")
                await websocket.close(code=4400, reason="Missing stream identifiers")
                return

            if not stream_id or not call_control_id:
                logger.error(
                    f"Empty stream identifiers: stream_id={stream_id}, "
                    f"call_control_id={call_control_id}"
                )
                await websocket.close(code=4400, reason="Missing stream identifiers")
                return

            logger.info(
                f"Telnyx stream started: stream_id={stream_id}, "
                f"call_control_id={call_control_id}, encoding={encoding}"
            )

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                user_id=user_id,
                call_id=call_control_id,
                transport_kwargs={
                    "stream_id": stream_id,
                    "call_control_id": call_control_id,
                    "encoding": encoding,
                },
            )

        except WebSocketDisconnect as e:
            # Telnyx opens the WebSocket during `bridging` (pre-answer) but only
            # sends the `start` event on `call.answered`. If the call ends before
            # answer (no-answer timeout, busy, declined), Telnyx closes the
            # socket abruptly — surface this as an expected end-of-call.
            logger.info(
                f"[run {workflow_run_id}] Telnyx WebSocket closed before stream start "
                f"(call ended pre-answer): code={e.code}, reason={e.reason!r}"
            )
        except Exception as e:
            logger.error(f"Error in Telnyx WebSocket handler: {e}")
            raise

    async def answer_and_stream(
        self, call_control_id: str, stream_url: str, webhook_url: str
    ) -> None:
        """Answer an inbound Telnyx call and start WebSocket streaming inline.

        This is Telnyx-specific: unlike Twilio/Vobiz where you return XML in the
        webhook response, Telnyx requires an explicit REST API call to answer
        the call and set up streaming.

        Args:
            call_control_id: The call_control_id from the inbound webhook
            stream_url: WebSocket URL for bidirectional audio streaming
            webhook_url: URL for status callback events
        """
        endpoint = f"{self.TELNYX_API_BASE}/calls/{call_control_id}/actions/answer"

        payload = {
            "stream_url": stream_url,
            "stream_track": "inbound_track",
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "PCMU",
            "webhook_url": webhook_url,
            "webhook_url_method": "POST",
        }

        logger.info(
            f"Answering Telnyx inbound call {call_control_id} with stream_url={stream_url}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, json=payload, headers=self._headers()
            ) as response:
                if response.status != 200:
                    error_data = await response.text()
                    logger.error(
                        f"Failed to answer Telnyx call {call_control_id}: "
                        f"status={response.status}, response={error_data}"
                    )
                    raise Exception(
                        f"Failed to answer Telnyx call: {response.status} {error_data}"
                    )

                logger.info(f"Successfully answered Telnyx call {call_control_id}")

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """Detect if a webhook is from Telnyx.

        Telnyx webhooks have a nested data.event_type structure
        and may include a telnyx-signature-ed25519 header.
        """
        if "telnyx-signature-ed25519" in headers:
            return True

        # Check for Telnyx event structure
        data = webhook_data.get("data", {})
        if data.get("record_type") == "event" and "event_type" in data:
            event_type = normalize_event_type(data.get("event_type", ""))
            if event_type.startswith("call."):
                return True

        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Parse Telnyx inbound webhook into normalized format."""
        data = webhook_data.get("data", webhook_data)
        payload = data.get("payload", {})

        # Telnyx uses "incoming" for inbound — normalize to "inbound"
        direction = payload.get("direction", "")
        if direction == "incoming":
            direction = "inbound"

        from_raw = payload.get("from", "")
        to_raw = payload.get("to", "")
        return NormalizedInboundData(
            provider=TelnyxProvider.PROVIDER_NAME,
            call_id=payload.get("call_control_id", ""),
            from_number=normalize_telephony_address(from_raw).canonical
            if from_raw
            else "",
            to_number=normalize_telephony_address(to_raw).canonical if to_raw else "",
            direction=direction,
            call_status=normalize_event_type(data.get("event_type", "")),
            account_id=payload.get("connection_id"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """Validate that the connection_id from webhook matches configuration."""
        if not webhook_account_id:
            return False
        return config_data.get("connection_id") == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """Required by the abstract interface. Telnyx signature verification
        (Ed25519 via ``telnyx-signature-ed25519``) is not yet implemented —
        accepts all inbound webhooks for now.
        """
        return True

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        """Update webhook_event_url on the Telnyx Call Control Application.

        PATCH requires application_name even on partial updates, so we GET
        first to preserve whatever name the user set in the cockpit. The URL
        is shared across every number on the application — clearing is a
        no-op to avoid silently breaking inbound for sibling numbers.
        """
        if webhook_url is None:
            logger.info(
                f"Telnyx configure_inbound clear for {address}: skipping "
                f"application update (webhook_event_url is shared across all "
                f"numbers on Call Control Application {self.connection_id})"
            )
            return ProviderSyncResult(ok=True)

        if not self.validate_config():
            return ProviderSyncResult(
                ok=False, message="Telnyx provider not properly configured"
            )

        if not self.connection_id:
            return ProviderSyncResult(
                ok=False,
                message=(
                    "Telnyx connection_id (Call Control Application ID) is "
                    "not configured. Set it in the telephony configuration "
                    "so inbound webhooks can be synced to the right "
                    "application."
                ),
            )

        app_endpoint = (
            f"{self.TELNYX_API_BASE}/call_control_applications/{self.connection_id}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    app_endpoint, headers=self._headers()
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.error(
                            f"Failed to fetch Telnyx Call Control Application "
                            f"{self.connection_id}: {response.status} {body}"
                        )
                        return ProviderSyncResult(
                            ok=False,
                            message=f"Telnyx API {response.status}: {body}",
                        )
                    app_data = await response.json()
        except Exception as e:
            logger.error(
                f"Exception fetching Telnyx Call Control Application "
                f"{self.connection_id}: {e}"
            )
            return ProviderSyncResult(ok=False, message=f"Telnyx lookup failed: {e}")

        application_name = (app_data.get("data") or {}).get("application_name")
        if not application_name:
            return ProviderSyncResult(
                ok=False,
                message=(
                    f"Telnyx Call Control Application {self.connection_id} "
                    f"did not return an application_name; cannot PATCH "
                    f"without it."
                ),
            )

        update_body = {
            "application_name": application_name,
            "webhook_event_url": webhook_url,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    app_endpoint, json=update_body, headers=self._headers()
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        logger.error(
                            f"Telnyx Call Control Application update failed "
                            f"for {self.connection_id}: {response.status} "
                            f"{body}"
                        )
                        return ProviderSyncResult(
                            ok=False,
                            message=f"Telnyx API {response.status}: {body}",
                        )
        except Exception as e:
            logger.error(
                f"Exception updating Telnyx Call Control Application "
                f"{self.connection_id}: {e}"
            )
            return ProviderSyncResult(ok=False, message=f"Telnyx update failed: {e}")

        logger.info(
            f"Telnyx webhook_event_url set on Call Control Application "
            f"{self.connection_id} (triggered by address {address})"
        )
        return ProviderSyncResult(ok=True)

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        """Answer the inbound Telnyx call via Call Control and start streaming.

        Unlike markup-response providers, Telnyx ignores webhook response
        bodies for call control — the call must be answered with a REST
        call back to Telnyx before media can flow. We do that here and
        return a simple acknowledgement; on failure, return the
        ANSWER_FAILED error response so the route stays provider-agnostic.
        """
        events_url = (
            f"{backend_endpoint}/api/v1/telephony/telnyx/events/{workflow_run_id}"
        )
        try:
            await self.answer_and_stream(
                call_control_id=normalized_data.call_id,
                stream_url=websocket_url,
                webhook_url=events_url,
            )
        except Exception as e:
            logger.error(f"Failed to answer Telnyx inbound call: {e}")
            return self.generate_error_response(
                "ANSWER_FAILED", "Failed to answer call"
            )
        return {"status": "ok"}

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

    # ======== CALL TRANSFER METHODS ========

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Telnyx call transfer is not yet implemented."""
        raise NotImplementedError("Call transfer not yet supported for Telnyx")

    def supports_transfers(self) -> bool:
        return False
