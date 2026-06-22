"""
Tone telephony provider implementation.

Tone (usetone.ai) provisions TRAI-compliant +91 numbers for AI agents,
built on Exotel as the underlying carrier.

Tone API: https://api.usetone.ai/v1
Auth:     Authorization: Bearer <api_key>

Verified Tone API call fields:
  Request:  to, from, callType, webhookUrl
  Response: id, status, to, from, callType, webhookUrl, createdAt

WebSocket protocol: Exotel Voicebot Applet (bidirectional)
  Audio: 8 kHz, base64-encoded; framed by pipecat.serializers.exotel
  Events: connected → start → media* → stop

Outbound only in this PR. Outbound flow:
  1. initiate_call() POSTs to Tone /v1/calls with webhookUrl pointing at
     /api/v1/telephony/tone-webhook?workflow_id=...&user_id=...&workflow_run_id=...&organization_id=...
  2. When the call connects, Tone hits that URL — handle_tone_webhook
     stores Exotel's CallSid into the run's gathered_context.
  3. Exotel then opens a WSS to
     /api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}
     which routes to handle_websocket() with full per-call context.

Inbound is NOT supported here — see configure_inbound() / start_inbound_stream().
"""

import hmac
import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket

TONE_API_BASE = "https://api.usetone.ai/v1"


class ToneProvider(TelephonyProvider):
    """Tone (usetone.ai) implementation of TelephonyProvider."""

    PROVIDER_NAME = WorkflowRunMode.TONE.value
    WEBHOOK_ENDPOINT = "tone-webhook"

    def __init__(self, config: Dict[str, Any]):
        self.api_key: str = config.get("api_key", "")
        self.from_numbers: List[str] = config.get("from_numbers", [])
        self.webhook_secret: Optional[str] = config.get("webhook_secret") or None

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        return bool(self.api_key and self.from_numbers)

    # ------------------------------------------------------------------
    # Outbound call
    # ------------------------------------------------------------------

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Initiate outbound call via Tone API.

        The ``webhook_url`` argument is built by the upstream caller
        (``api/routes/telephony.py``) and already contains all four query
        params required by ``handle_tone_webhook``: workflow_id, user_id,
        workflow_run_id, organization_id. We pass it through unchanged.

        Recognized kwargs:
            callType (str): TRANSACTIONAL or PROMOTIONAL; defaults to
                TRANSACTIONAL.
        """
        if not self.validate_config():
            raise ValueError("Tone provider not properly configured")

        _from = from_number or random.choice(self.from_numbers)

        # Tone API POST /v1/calls — verified field names
        payload: Dict[str, Any] = {
            "to": to_number,
            "from": _from,
            "callType": kwargs.pop("callType", "TRANSACTIONAL"),
            "webhookUrl": webhook_url,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{TONE_API_BASE}/calls",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as response:
                response_text = await response.text()
                if response.status not in (200, 201, 202):
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to initiate Tone call: {response_text}",
                    )

                response_data = json.loads(response_text)
                call_id = response_data.get("id", "")

                if not call_id:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Tone response missing call id: {response_data}",
                    )

                return CallInitiationResult(
                    call_id=call_id,
                    status=response_data.get("status", "queued"),
                    caller_number=_from,
                    provider_metadata={"call_id": call_id},
                    raw_response=response_data,
                )

    # ------------------------------------------------------------------
    # Call status
    # ------------------------------------------------------------------

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TONE_API_BASE}/calls/{call_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as response:
                if response.status != 200:
                    error = await response.text()
                    raise Exception(f"Failed to get Tone call status: {error}")
                return await response.json()

    # ------------------------------------------------------------------
    # Phone numbers
    # ------------------------------------------------------------------

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    # ------------------------------------------------------------------
    # Webhook response
    # (Tone/Exotel doesn't use XML — WSS URL is set in App Bazaar)
    # Required by abstract base but not used for this provider.
    # ------------------------------------------------------------------

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        # Tone does not use TwiML or Plivo XML. The WebSocket URL is configured
        # statically in the Exotel App Bazaar Voicebot Applet. This method is
        # defined to satisfy the abstract interface but should not be called.
        logger.warning(
            "[Tone] get_webhook_response called but Tone uses Exotel App Bazaar "
            "for WebSocket URL configuration, not webhook XML responses."
        )
        return ""

    # ------------------------------------------------------------------
    # Webhook / inbound signature — Exotel has no HMAC
    # ------------------------------------------------------------------

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        # Exotel does not send an HMAC signature on HTTP callbacks.
        # Auth is via IP whitelisting or Basic Auth in the callback URL.
        # Always return True — harden via IP whitelist in production.
        return True

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        # Exotel has no HMAC. Use shared-secret header instead.
        # If no secret configured → backward-compat allow (warn).
        if not self.webhook_secret:
            logger.warning(
                "[Tone] verify_inbound_signature: no webhook_secret configured — "
                "callback authentication is DISABLED. Set webhook_secret in provider "
                "config to enable X-Tone-Webhook-Secret verification."
            )
            return True

        # Header lookup is case-insensitive — Starlette gives lowercase keys.
        provided = (
            headers.get("X-Tone-Webhook-Secret")
            or headers.get("x-tone-webhook-secret")
            or ""
        )
        if not provided:
            return False
        return hmac.compare_digest(provided, self.webhook_secret)

    # ------------------------------------------------------------------
    # Status callback normalization
    # ------------------------------------------------------------------

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Exotel Passthru applet sends form-encoded params
        status_map = {
            "initiated":  "initiated",
            "ringing":    "ringing",
            "in-progress": "answered",
            "answered":   "answered",
            "completed":  "completed",
            "failed":     "failed",
            "busy":       "busy",
            "no-answer":  "no-answer",
            "canceled":   "canceled",
            "cancelled":  "canceled",
        }

        raw_status = (data.get("Status") or data.get("status") or "").lower()

        return {
            "call_id":      data.get("CallSid") or data.get("id", ""),
            "status":       status_map.get(raw_status, raw_status),
            "from_number":  data.get("From") or data.get("from"),
            "to_number":    data.get("To") or data.get("to"),
            "direction":    data.get("Direction"),
            "duration":     data.get("Duration") or data.get("duration"),
            "extra":        data,
        }

    # ------------------------------------------------------------------
    # WebSocket audio (Exotel Voicebot Applet)
    # ------------------------------------------------------------------

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """Handle Exotel Voicebot Applet bidirectional WebSocket.

        OUTBOUND ONLY in this PR. This method is reached when an outbound call
        initiated via ``initiate_call()`` connects: the workflow_run_id is
        already known at dispatch time and embedded in the WSS URL Exotel
        connects to ( /api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id} ).

        INBOUND is not supported in this PR. Exotel App Bazaar requires a
        single static WSS URL per Voicebot Applet, but workflow_run_id is
        only created when an inbound call arrives. A separate endpoint that
        creates the workflow_run on WebSocket connect would be needed —
        out of scope here.
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        # Exotel sends: {"event":"connected"} then {"event":"start", "start":{...}}
        stream_sid = None
        call_sid = None

        for _ in range(2):
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            if msg.get("event") == "start":
                start = msg.get("start", {})
                stream_sid = start.get("stream_sid") or msg.get("stream_sid")
                call_sid = start.get("call_sid")
                break

        if not stream_sid:
            logger.error(f"[Tone] Missing stream_sid in start event for run {workflow_run_id}")
            await websocket.close(code=4400, reason="Missing stream_sid")
            return

        # Resolve call_id from DB context (stored by tone-webhook) or from start event
        if not call_sid:
            workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
            if workflow_run and workflow_run.gathered_context:
                call_sid = workflow_run.gathered_context.get("call_id")

        logger.info(
            f"[Tone] WebSocket connected: stream_sid={stream_sid} "
            f"call_sid={call_sid} run={workflow_run_id}"
        )

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            call_id=call_sid or "",
            transport_kwargs={"stream_sid": stream_sid, "call_sid": call_sid},
        )

    # ------------------------------------------------------------------
    # Call cost
    # ------------------------------------------------------------------

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        try:
            call_data = await self.get_call_status(call_id)
            return {
                "cost_usd":     float(call_data.get("cost") or 0),
                "duration":     int(call_data.get("duration") or 0),
                "status":       call_data.get("status", "unknown"),
                "price_unit":   "INR",  # Tone is India-first
                "raw_response": call_data,
            }
        except Exception as e:
            logger.error(f"[Tone] Exception fetching call cost for {call_id}: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}

    # ------------------------------------------------------------------
    # Inbound call support
    # ------------------------------------------------------------------

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        # Both Tone/Exotel and Twilio send CallSid, so CallSid alone is
        # ambiguous. Discriminate against Twilio by:
        #   - Twilio sends x-twilio-signature header; Exotel does not.
        #   - Twilio's AccountSid starts with "AC"; Exotel's does not.
        account_sid = webhook_data.get("AccountSid", "")
        lower_headers = {k.lower(): v for k, v in headers.items()}
        has_twilio_sig = "x-twilio-signature" in lower_headers
        return (
            "CallSid" in webhook_data
            and not has_twilio_sig
            and not account_sid.startswith("AC")
        )

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        from_raw = webhook_data.get("From", "")
        to_raw = webhook_data.get("To", "")
        return NormalizedInboundData(
            provider=ToneProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallSid", ""),
            from_number=normalize_telephony_address(from_raw).canonical if from_raw else "",
            to_number=normalize_telephony_address(to_raw).canonical if to_raw else "",
            direction=webhook_data.get("Direction", "inbound"),
            call_status=webhook_data.get("Status", ""),
            account_id=webhook_data.get("AccountSid"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        # Exotel doesn't send a consistent account_id in all webhooks; allow through
        if not webhook_account_id:
            return bool(config_data.get("api_key"))
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        # Inbound call support is NOT implemented in this PR.
        # Exotel App Bazaar requires a static WSS URL per Voicebot Applet,
        # but workflow_run_id is only known at dispatch time. A separate
        # endpoint that creates the workflow_run on WebSocket connect would
        # be needed. Returning 200 here is a no-op so the dispatcher doesn't
        # error, but the call will not route correctly.
        from fastapi.responses import JSONResponse
        return JSONResponse(content={"status": "ok"})

    # ------------------------------------------------------------------
    # Error responses
    # ------------------------------------------------------------------

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"error": error_type, "message": message},
            status_code=400,
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"error": str(error_type), "message": "Validation failed"},
            status_code=401,
        )

    # ------------------------------------------------------------------
    # Call transfers
    # ------------------------------------------------------------------

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Tone provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Inbound configuration (App Bazaar — manual, not programmable via REST)
    # ------------------------------------------------------------------

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        # Inbound call support is NOT implemented in this PR. Exotel App Bazaar
        # requires a static WSS URL, but Dograh's WSS endpoint
        # (/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id})
        # contains a per-call workflow_run_id that doesn't exist until the call
        # arrives. Inbound support will require a separate endpoint that creates
        # the workflow_run on WebSocket connect.
        logger.info(
            f"[Tone] configure_inbound called for {address} — no-op. "
            "Inbound is not supported in this PR version."
        )
        return ProviderSyncResult(
            ok=True,
            message=(
                "Inbound calls are not supported by the Tone provider in this "
                "PR. Outbound calls work via initiate_call()."
            ),
        )
