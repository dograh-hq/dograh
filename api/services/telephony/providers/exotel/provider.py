"""
Exotel implementation of the TelephonyProvider interface.

Exotel Voice v1 API:
  Base URL: https://{api_key}:{api_token}@{subdomain}/v1/Accounts/{account_sid}/
  Outbound: POST /Calls/connect
  Call details: GET /Calls/{CallSid}.json

Audio format: 8 kHz μ-law (same wire format as Twilio / Plivo).
"""

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
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket


class ExotelProvider(TelephonyProvider):
    """
    Exotel Voice v1 implementation of TelephonyProvider.

    Credentials required:
      - api_key     : from Exotel Dashboard → Settings → API Settings
      - api_token   : same location
      - account_sid : your Exotel account SID / subdomain identifier
      - subdomain   : e.g. api.exotel.com (global) or api.in.exotel.com (India)
    """

    PROVIDER_NAME = WorkflowRunMode.EXOTEL.value
    WEBHOOK_ENDPOINT = "exotel-xml"  # path under /api/v1/telephony

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.api_token = config.get("api_token", "")
        self.account_sid = config.get("account_sid", "")
        self.subdomain = config.get("subdomain", "api.exotel.com")
        self.from_numbers = config.get("from_numbers", [])
        self.app_id = config.get("app_id")

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self._base_url = (
            f"https://{self.api_key}:{self.api_token}"
            f"@{self.subdomain}/v1/Accounts/{self.account_sid}"
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _calls_url(self) -> str:
        return f"{self._base_url}/Calls"

    def _call_url(self, call_sid: str) -> str:
        return f"{self._base_url}/Calls/{call_sid}.json"

    # -------------------------------------------------------------------------
    # Outbound
    # -------------------------------------------------------------------------

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Initiate an outbound call via Exotel Calls/connect.

        ``From``     — the number that gets called first (the callee / end-user).
        ``CallerId`` — the ExoPhone (Exotel virtual number) shown as caller ID.
        ``Url``      — Exotel app/flow URL; we point it at our answer webhook.
        """
        if not self.validate_config():
            raise ValueError("Exotel provider not properly configured")

        caller_id = from_number or random.choice(self.from_numbers)

        data: Dict[str, Any] = {
            "From": to_number,
            "CallerId": caller_id,
            "Url": webhook_url,
            "CallType": "trans",  # transactional — no recording by default
        }

        if workflow_run_id:
            backend_endpoint, _ = await get_backend_endpoints()
            data["StatusCallback"] = (
                f"{backend_endpoint}/api/v1/telephony/exotel/status-callback/{workflow_run_id}"
            )

        data.update(kwargs)

        endpoint = f"{self._calls_url()}/connect"
        logger.info(
            f"[Exotel] Initiating outbound call to {to_number} "
            f"via CallerID={caller_id}, workflow_run_id={workflow_run_id}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, data=data) as response:
                response_text = await response.text()
                if response.status not in (200, 201, 202):
                    logger.error(
                        f"[Exotel] Calls/connect failed: "
                        f"HTTP {response.status} body={response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to initiate Exotel call: {response_text}",
                    )

                try:
                    response_data = json.loads(response_text)
                except json.JSONDecodeError:
                    logger.error(f"[Exotel] Non-JSON response: {response_text}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Exotel returned non-JSON response: {response_text}",
                    )

                call_obj = response_data.get("Call", {})
                call_sid = call_obj.get("Sid")
                if not call_sid:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Exotel response missing Call.Sid: {response_data}",
                    )

                logger.info(
                    f"[Exotel] Outbound call placed: Sid={call_sid} "
                    f"Status={call_obj.get('Status')}"
                )
                return CallInitiationResult(
                    call_id=call_sid,
                    status=call_obj.get("Status", "queued"),
                    caller_number=caller_id,
                    provider_metadata={"call_sid": call_sid},
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        if not self.validate_config():
            raise ValueError("Exotel provider not properly configured")

        async with aiohttp.ClientSession() as session:
            async with session.get(self._call_url(call_id)) as response:
                if response.status != 200:
                    error_data = await response.text()
                    raise Exception(
                        f"[Exotel] Failed to get call status: {error_data}"
                    )
                data = await response.json(content_type=None)
                return data.get("Call", data)

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        try:
            call_data = await self.get_call_status(call_id)
            price_str = call_data.get("Price") or "0"
            try:
                cost = float(price_str)
            except (ValueError, TypeError):
                cost = 0.0

            duration_str = call_data.get("Duration") or "0"
            try:
                duration = int(duration_str)
            except (ValueError, TypeError):
                duration = 0

            return {
                "cost_usd": cost,
                "duration": duration,
                "status": call_data.get("Status", "unknown"),
                "price_unit": "INR",  # Exotel prices in INR
                "raw_response": call_data,
            }
        except Exception as e:
            logger.error(f"[Exotel] Exception fetching call cost for {call_id}: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(
            self.api_key
            and self.api_token
            and self.account_sid
            and self.from_numbers
        )

    # -------------------------------------------------------------------------
    # Webhooks / answer URL
    # -------------------------------------------------------------------------

    async def verify_webhook_signature(
        self,
        url: str,
        params: Dict[str, Any],
        signature: str,
    ) -> bool:
        # Exotel v1 does not sign webhooks with a secret. Accept all.
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """Return ExoML that streams audio over WebSocket (μ-law 8 kHz)."""
        _, wss_backend_endpoint = await get_backend_endpoints()
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f'    <Stream bidirectional="true" keepCallAlive="true" '
            f'contentType="audio/x-mulaw;rate=8000">'
            f"{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}"
            f"</Stream>\n"
            f"</Response>"
        )

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Exotel StatusCallback POST fields."""
        status_map = {
            "queued": "queued",
            "in-progress": "answered",
            "completed": "completed",
            "failed": "failed",
            "busy": "busy",
            "no-answer": "no-answer",
            "answered": "answered",
            "terminal": "completed",
        }
        raw_status = (data.get("Status") or data.get("EventType") or "").lower()
        call_sid = data.get("CallSid", "")
        return {
            "call_id": call_sid,
            "status": status_map.get(raw_status, raw_status),
            "from_number": data.get("From"),
            "to_number": data.get("To"),
            "direction": data.get("Direction"),
            "duration": data.get("ConversationDuration") or data.get("Duration"),
            "extra": data,
        }

    # -------------------------------------------------------------------------
    # WebSocket
    # -------------------------------------------------------------------------

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        # Exotel sends a JSON "start" event first (same pattern as Plivo/Twilio)
        first_msg = await websocket.receive_text()
        start_msg = json.loads(first_msg)

        if start_msg.get("event") != "start":
            logger.error(
                f"[Exotel] Expected 'start' event, got: {start_msg.get('event')}"
            )
            await websocket.close(code=4400, reason="Expected start event")
            return

        start_data = start_msg.get("start", {})
        stream_id = start_data.get("streamId") or start_msg.get("streamId", "")

        if not stream_id:
            logger.error(f"[Exotel] Missing streamId in start event: {start_msg}")
            await websocket.close(code=4400, reason="Missing streamId")
            return

        # Prefer call_id stored on the workflow run (populated by the answer webhook)
        workflow_run = await db_client.get_workflow_run(workflow_run_id)
        call_id = None
        if workflow_run and workflow_run.gathered_context:
            call_id = workflow_run.gathered_context.get("call_id")

        if not call_id:
            call_id = (
                start_data.get("callId")
                or start_data.get("callSid")
                or start_data.get("CallSid")
            )

        if not call_id:
            logger.error(
                f"[Exotel] Missing call ID for workflow run {workflow_run_id}"
            )
            await websocket.close(code=4400, reason="Missing call ID")
            return

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            call_id=call_id,
            transport_kwargs={"stream_id": stream_id, "call_id": call_id},
        )

    # -------------------------------------------------------------------------
    # Inbound
    # -------------------------------------------------------------------------

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        # Exotel inbound webhooks carry CallSid and AccountSid but no
        # provider-specific header. We match on AccountSid presence.
        return "CallSid" in webhook_data and "AccountSid" in webhook_data

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        from_raw = webhook_data.get("From", "")
        to_raw = webhook_data.get("To", "") or webhook_data.get("PhoneNumberSid", "")
        return NormalizedInboundData(
            provider=ExotelProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallSid", ""),
            from_number=normalize_telephony_address(from_raw).canonical
            if from_raw
            else "",
            to_number=normalize_telephony_address(to_raw).canonical
            if to_raw
            else "",
            direction=webhook_data.get("Direction", "inbound"),
            call_status=webhook_data.get("Status", ""),
            account_id=webhook_data.get("AccountSid"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if webhook_account_id:
            return config_data.get("account_sid") == webhook_account_id
        logger.warning(
            "[Exotel] Inbound webhook missing AccountSid — "
            "falling back to config existence check"
        )
        return bool(config_data.get("account_sid"))

    def normalize_phone_number(self, phone_number: str) -> str:
        return phone_number

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        # Exotel v1 does not sign inbound webhooks.
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ):
        from fastapi import Response

        hangup_callback_attr = ""
        if workflow_run_id:
            hangup_url = (
                f"{backend_endpoint}/api/v1/telephony/exotel"
                f"/status-callback/{workflow_run_id}"
            )
            hangup_callback_attr = (
                f' statusCallbackUrl="{hangup_url}" statusCallbackMethod="POST"'
            )

        exo_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f'    <Stream bidirectional="true" keepCallAlive="true" '
            f'contentType="audio/x-mulaw;rate=8000"'
            f"{hangup_callback_attr}>"
            f"{websocket_url}"
            f"</Stream>\n"
            f"</Response>"
        )
        return Response(content=exo_xml, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi import Response

        exo_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response>\n"
            f"    <Say>Sorry, there was an error processing your call. {message}</Say>\n"
            f"    <Hangup/>\n"
            f"</Response>"
        )
        return Response(content=exo_xml, media_type="application/xml")

    # -------------------------------------------------------------------------
    # Transfers (not supported)
    # -------------------------------------------------------------------------

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Exotel provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False

    # -------------------------------------------------------------------------
    # Optional: configure inbound (no-op — Exotel doesn't support programmatic
    # answer-URL binding via the v1 REST API)
    # -------------------------------------------------------------------------

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        logger.info(
            f"[Exotel] configure_inbound called for {address} → {webhook_url}. "
            "Exotel v1 does not support programmatic webhook binding; "
            "configure the answer URL in the Exotel App Bazaar."
        )
        return ProviderSyncResult(ok=True)
