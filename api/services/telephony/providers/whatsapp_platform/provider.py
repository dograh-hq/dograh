"""
WhatsApp Platform implementation of TelephonyProvider.

Architecture
────────────
Inbound flow (customer calls → AI answers):
  Customer WhatsApp call
    → Meta webhook → Platform Node.js backend
    → dograh-bridge.js (server-side WebRTC via @roamhq/wrtc)
    → Dograh WebSocket audio stream (16kHz PCM)
    → Dograh Pipecat pipeline (STT → LLM → TTS)
    → dograh-bridge.js (PCM injected back to WebRTC track)
    → Customer hears AI voice

Transfer to human:
  Pipecat pipeline calls WhatsAppTransferStrategy.execute_transfer()
    → POST /dograh-webhook/transfer on Platform backend
    → Platform re-broadcasts call:incoming to human agents

Hangup by AI:
  Pipecat pipeline calls WhatsAppHangupStrategy.execute_hangup()
    → POST /dograh-webhook/hangup on Platform backend
    → Platform calls Meta terminate API

Outbound calls are NOT supported (WhatsApp Business Policy).
"""

from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)


class WhatsAppPlatformProvider(TelephonyProvider):
    """
    WhatsApp Platform telephony provider.

    The audio bridge (Node.js) handles all WebRTC complexity.
    This provider's job is:
      1. Provide a WebSocket endpoint for the bridge to connect to.
      2. Implement transfer + hangup via HTTP callbacks to the platform.
      3. Save post-call transcript via the call-ended webhook.
    """

    PROVIDER_NAME = "whatsapp_platform"
    # The bridge connects directly to Dograh's audio-stream WebSocket.
    # No separate inbound webhook URL is needed — the bridge is the caller.
    WEBHOOK_ENDPOINT = None

    def __init__(self, config: Dict[str, Any]):
        self.platform_api_url = config.get("platform_api_url", "").rstrip("/")
        self.webhook_secret   = config.get("webhook_secret", "")

    # ── Outbound (not supported) ───────────────────────────────────────────────

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Not supported. WhatsApp Business Policy prohibits unsolicited calls."""
        raise NotImplementedError(
            "WhatsApp Platform does not support outbound calls. "
            "Inbound calls only (customer initiates, AI answers)."
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Call status is managed by the Platform backend, not polled by Dograh."""
        return {"call_id": call_id, "status": "unknown", "provider": self.PROVIDER_NAME}

    async def get_available_phone_numbers(self) -> List[str]:
        """Phone numbers are managed by the Platform (WhatsApp numbers)."""
        return []

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """No per-minute cost — WhatsApp calls are free to receive."""
        return {"call_id": call_id, "cost": 0.0, "currency": "USD"}

    # ── Inbound normalisation ─────────────────────────────────────────────────

    async def normalize_inbound_data(
        self, raw_data: Dict[str, Any]
    ) -> NormalizedInboundData:
        """
        Normalise inbound call data sent by the bridge when it starts a run.

        The bridge passes these fields via the initial_context of the
        trigger-workflow API call:
          - wa_call_id:   Meta call ID
          - caller_phone: Caller's WhatsApp number (+E.164)
          - caller_name:  Display name (may be None)
        """
        return NormalizedInboundData(
            provider=self.PROVIDER_NAME,
            call_id=raw_data.get("wa_call_id", ""),
            from_number=raw_data.get("caller_phone", ""),
            to_number=raw_data.get("to_number", ""),
            direction="inbound",
            call_status="ringing",
            raw_data=raw_data,
        )

    async def validate_inbound_request(
        self, request_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Validate that an inbound request came from our bridge.
        The bridge sends the webhook_secret in X-Dograh-Key header.
        """
        provided = headers.get("x-dograh-key", "")
        return bool(provided and provided == self.webhook_secret)

    # ── Post-call callbacks (called by Pipecat pipeline) ──────────────────────

    async def save_recording(
        self, call_id: str, recording_url: str, **kwargs: Any
    ) -> bool:
        """Notify the Platform to save the call recording URL."""
        return await self._platform_post(
            "/dograh-webhook/call-ended",
            {
                "wa_call_id": call_id,
                "recording_url": recording_url,
                **kwargs,
            },
        )

    async def save_transcript(
        self, call_id: str, transcript: str, outcome: Optional[str] = None, **kwargs: Any
    ) -> bool:
        """Push the full transcript + outcome to the Platform backend."""
        return await self._platform_post(
            "/dograh-webhook/call-ended",
            {
                "wa_call_id":  call_id,
                "transcript":  transcript,
                "outcome":     outcome,
                **kwargs,
            },
        )

    # ── Agent Stream Entry Point ──────────────────────────────────────────────
    async def handle_external_websocket(
        self,
        websocket: "WebSocket",
        *,
        organization_id: int,
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
        params: Dict[str, str],
    ) -> None:
        """Agent-stream entry point for WhatsApp bridge."""
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        logger.info(
            f"[WhatsAppPlatform] Agent stream connected for run {workflow_run_id}, "
            f"workflow {workflow_id}"
        )

        await run_pipeline_telephony(
            websocket=websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            call_id=params.get("callId", ""),
            transport_kwargs={"params": params},
        )

    # ── Config sync ───────────────────────────────────────────────────────────

    async def configure_inbound(self, **kwargs: Any) -> ProviderSyncResult:
        """No upstream provider config to push — phone numbers live in the Platform."""
        return ProviderSyncResult(ok=True, message="WhatsApp Platform configured (no upstream sync needed)")

    def validate_config(self) -> bool:
        return bool(self.platform_api_url and self.webhook_secret)

    # ── Missing abstract methods ──────────────────────────────────────────────

    def can_handle_webhook(self, request_path: str) -> bool:
        return False

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        return {"error": message}, "application/json"

    async def get_webhook_response(self, workflow_id: int, user_id: int, workflow_run_id: int) -> str:
        return "{}"

    async def handle_websocket(self, websocket: Any, workflow_id: int, user_id: int, workflow_run_id: int) -> None:
        raise NotImplementedError("Use handle_external_websocket instead")

    def parse_inbound_webhook(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    async def start_inbound_stream(self, data: Dict[str, Any], workflow_id: int, user_id: int, workflow_run_id: int) -> tuple:
        return {"error": "Not used"}, "application/json"

    def supports_transfers(self) -> bool:
        return True

    async def transfer_call(self, destination: str, transfer_id: str, conference_name: str, timeout: int = 30, **kwargs: Any) -> Dict[str, Any]:
        """Handled by WhatsAppTransferStrategy via save_transcript/etc"""
        return {"status": "transferring"}

    async def validate_account_id(self, account_id: str) -> bool:
        return True

    async def verify_inbound_signature(self, headers: Dict[str, str], raw_body: bytes) -> bool:
        return True

    async def verify_webhook_signature(self, url: str, params: Dict[str, Any], signature: str) -> bool:
        return True

    # ── Internal helper ───────────────────────────────────────────────────────

    async def _platform_post(self, path: str, body: Dict[str, Any]) -> bool:
        """POST to the WhatsApp Platform backend with the webhook secret header."""
        url = self.platform_api_url + path
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "X-Dograh-Key":  self.webhook_secret,
                        "Content-Type":  "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if not resp.ok:
                        text = await resp.text()
                        logger.warning(
                            f"[WhatsAppPlatform] POST {path} → HTTP {resp.status}: {text[:200]}"
                        )
                        return False
                    return True
        except Exception as exc:
            logger.error(f"[WhatsAppPlatform] POST {path} failed: {exc}")
            return False
