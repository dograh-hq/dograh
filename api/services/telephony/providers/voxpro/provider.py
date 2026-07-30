"""VoxPro (India VNO) implementation of the TelephonyProvider interface.

VoxPro is a managed carrier: instead of exposing raw Asterisk/ARI, the Dograh
user authenticates to VoxPro's connector API with a per-tenant API key. The
connector originates/bridges the PSTN call on VoxPro's own Asterisk + carrier
trunk and streams Plivo/Twilio-standard mu-law audio to Dograh over a WebSocket.

Call control (originate / transfer / hangup / status) is REST — this is a
call-control provider, like Telnyx — while media is the Plivo-compatible
WebSocket, so the published Plivo serializer/transport are reused.
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints

if TYPE_CHECKING:
    from fastapi import WebSocket

# Contract with the transfer tool: `api/services/workflow/pipecat_engine_custom_tools.py`
# names the transfer conference `transfer-{original_call_sid}`. This constant makes
# that coupling explicit — keep it in sync if the convention changes upstream.
TRANSFER_CONFERENCE_PREFIX = "transfer-"


class VoxProProvider(TelephonyProvider):
    """VoxPro managed-carrier implementation of TelephonyProvider."""

    PROVIDER_NAME = WorkflowRunMode.VOXPRO.value
    # Call-control provider (like ARI): the call lifecycle is driven by the media
    # WebSocket + the VoxPro connector, so there is no HTTP status-callback route.
    WEBHOOK_ENDPOINT = None

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key")
        self.tenant_id = config.get("tenant_id")
        self.api_base = (config.get("api_base") or "").rstrip("/")
        self.from_numbers = config.get("from_numbers", [])
        self.default_from_number = config.get("default_from_number")
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-Key": self.api_key or "",
            "X-Tenant-ID": self.tenant_id or "",
            "Content-Type": "application/json",
        }

    # ── Outbound ────────────────────────────────────────────────────────
    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        if not self.validate_config():
            raise ValueError("VoxPro provider not properly configured")

        from_number = self.select_from_number(from_number)

        # Media flows over Dograh's generic telephony WebSocket route, which
        # dispatches to VoxProProvider.handle_websocket. It needs all three ids;
        # workflow_id + organization_id arrive via kwargs (as for Telnyx).
        workflow_id = kwargs.pop("workflow_id", None)
        organization_id = kwargs.pop("organization_id", None)
        if not workflow_run_id or workflow_id is None or organization_id is None:
            raise ValueError(
                "VoxPro initiate_call requires workflow_run_id, workflow_id and organization_id"
            )

        _, ws_base = await get_backend_endpoints()
        ws_url = (
            f"{ws_base}/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}"
        )

        # The connector originates on VoxPro's carrier and connects the media leg
        # to this WebSocket, streaming Plivo/Twilio-standard mu-law audio.
        payload = {
            "to_number": to_number.lstrip("+"),
            "from_number": (from_number or "").lstrip("+"),
            "ws_url": ws_url,
            "workflow_run_id": str(workflow_run_id),
        }
        payload.update(kwargs)

        endpoint = f"{self.api_base}/v1/calls/originate"
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=self._headers()) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"[VoxPro] originate failed: HTTP {resp.status} {body}")
                    raise HTTPException(
                        status_code=resp.status,
                        detail=f"Failed to initiate VoxPro call: {body}",
                    )
                data = await resp.json()

        call_id = data.get("call_id") or data.get("session_uuid", "")
        logger.info(f"[VoxPro] call initiated {from_number} -> {to_number} (id={call_id})")
        return CallInitiationResult(
            call_id=call_id,
            status=data.get("state", "initiated"),
            caller_number=from_number,
            # Persisting call_id here is important: the dispatcher merges
            # provider_metadata into gathered_context, and transfer_call recovers
            # the active call id from gathered_context['call_id'] (via
            # conference_name). VoxPro has no status-callback path that would
            # otherwise populate it (cf. Telnyx, which does the same).
            provider_metadata={"call_id": call_id, "session_uuid": data.get("session_uuid")},
            raw_response=data,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        endpoint = f"{self.api_base}/v1/calls/{call_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=self._headers()) as resp:
                if resp.status != 200:
                    return {"call_id": call_id, "status": "unknown"}
                return await resp.json()

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        # VoxPro billing is reconciled out-of-band; expose a stable shape.
        return {"call_id": call_id, "currency": "INR", "amount": None}

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(self.api_key and self.tenant_id and self.api_base and self.from_numbers)

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "call_id": data.get("call_id") or data.get("session_uuid", ""),
            "status": data.get("state") or data.get("status", ""),
            "raw": data,
        }

    # ── Webhook signing (status callbacks) ──────────────────────────────
    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str, *args, **kwargs
    ) -> bool:
        # HMAC-SHA256 of the raw body with the tenant api_key. Fail closed.
        if not signature or not self.api_key:
            return False
        body = kwargs.get("body") or (args[0] if args else "") or json.dumps(params, separators=(",", ":"))
        expected = hmac.new(self.api_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        # Call-control provider: no markup document to return.
        return ""

    # ── Media WebSocket ─────────────────────────────────────────────────
    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """Handle the VoxPro media WebSocket (Plivo-compatible start event)."""
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        first_msg = await websocket.receive_text()
        start_msg = json.loads(first_msg)

        if start_msg.get("event") != "start":
            logger.error(f"[VoxPro] expected 'start' event, got: {start_msg.get('event')}")
            await websocket.close(code=4400, reason="Expected start event")
            return

        start_data = start_msg.get("start", {})
        stream_id = start_data.get("streamId")
        call_id = start_data.get("callId")
        if not stream_id or not call_id:
            logger.error(f"[VoxPro] missing streamId/callId in start event: {start_data}")
            await websocket.close(code=4400, reason="Missing streamId or callId")
            return

        # VoxPro has no status-callback route, so persist the connector's call_id
        # here (merged into gathered_context) — this is what transfer_call reads
        # back via conference_name. Covers inbound, where initiate_call never ran.
        try:
            from api.db import db_client

            await db_client.update_workflow_run(
                run_id=workflow_run_id, gathered_context={"call_id": call_id}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[VoxPro] could not persist call_id to gathered_context: {exc}")

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_id=call_id,
            transport_kwargs={"stream_id": stream_id, "call_id": call_id},
        )

    # ── Inbound ─────────────────────────────────────────────────────────
    @classmethod
    def can_handle_webhook(cls, webhook_data: Dict[str, Any], headers: Dict[str, str]) -> bool:
        ua = headers.get("user-agent", "").lower()
        return "voxpro" in ua or "x-voxpro-tenant" in {k.lower() for k in headers}

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        return NormalizedInboundData(
            provider=VoxProProvider.PROVIDER_NAME,
            call_id=webhook_data.get("call_id", "") or webhook_data.get("session_uuid", ""),
            from_number=webhook_data.get("from", ""),
            to_number=webhook_data.get("to", ""),
            direction="inbound",
            call_status=webhook_data.get("status", "ringing"),
            account_id=webhook_data.get("tenant_id"),
            from_country="IN",
            to_country="IN",
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if not webhook_account_id:
            return False
        return config_data.get("tenant_id") == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        normalized = {k.lower(): v for k, v in headers.items()}
        signature = normalized.get("x-voxpro-signature", "")
        if not signature:
            logger.warning("[VoxPro] inbound webhook missing X-VoxPro-Signature")
            return False
        return await self.verify_webhook_signature(url, webhook_data, signature, body=body)

    @classmethod
    async def start_inbound_stream(
        cls,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: "NormalizedInboundData",
        backend_endpoint: str,
    ):
        """Call-control inbound: tell the connector where to stream, then ack.

        The connector already holds the inbound call; we hand it the Dograh
        media WebSocket URL for this run and return a simple acknowledgement
        (this provider is call-control, not markup).
        """
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {
                "action": "stream",
                "websocket_url": websocket_url,
                "workflow_run_id": workflow_run_id,
                "call_id": normalized_data.call_id,
            }
        )

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"success": False, "error": error_type, "message": message}, status_code=400
        )

    # ── Transfers (VoxPro supports them) ────────────────────────────────
    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Dograh does not pass the active call id to transfer_call, but
        # conference_name is "transfer-{original_call_sid}" (see
        # workflow/pipecat_engine_custom_tools). VoxPro controls Asterisk, so we
        # transfer that real call directly rather than using the conference
        # bridge cloud providers rely on.
        call_id = kwargs.get("call_id")
        if not call_id and conference_name and conference_name.startswith(TRANSFER_CONFERENCE_PREFIX):
            call_id = conference_name[len(TRANSFER_CONFERENCE_PREFIX):]
        if not call_id:
            raise ValueError(
                "VoxPro transfer_call could not resolve the active call id "
                "(expected conference_name 'transfer-<call_id>' or kwargs['call_id'])"
            )

        endpoint = f"{self.api_base}/v1/calls/{call_id}/transfer"
        payload = {
            "destination": destination.lstrip("+"),
            "blind": kwargs.get("blind", True),
            "transfer_id": transfer_id,
            "timeout": timeout,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=self._headers()) as resp:
                if resp.status not in (200, 202):
                    body = await resp.text()
                    raise HTTPException(status_code=resp.status,
                                        detail=f"VoxPro transfer failed: {body}")
                data = await resp.json()
        return {"call_sid": call_id, "status": "transferring",
                "provider": self.PROVIDER_NAME, "raw": data}

    def supports_transfers(self) -> bool:
        return True
