"""Tata Smartflo Telephony Provider implementation."""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
import httpx
from loguru import logger
from starlette.websockets import WebSocket

from api.enums import TelephonyCallStatus
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.services.telephony.providers.smartflo.credential_resolver import (
    mask_phone_number,
    resolve_smartflo_credentials,
)
from api.services.telephony.providers.smartflo.redis_state import (
    save_smartflo_call_state,
)


class SmartfloProvider(TelephonyProvider):
    """Telephony provider implementation for Tata Smartflo (TTBS)."""

    PROVIDER_NAME = "smartflo"
    WEBHOOK_ENDPOINT = "smartflo_connect"

    def __init__(self, config: Dict[str, Any]):
        self.config = config or {}
        self.click_to_call_api_key = self.config.get("click_to_call_api_key")
        self.smartflo_jwt_token = self.config.get("smartflo_jwt_token")
        self.smartflo_did_number = self.config.get("smartflo_did_number")
        self.api_domain = (
            self.config.get("smartflo_api_domain")
            or "https://api-smartflo.tatateleservices.com"
        ).rstrip("/")

        self.from_numbers = self.config.get("from_numbers", [])
        if self.smartflo_did_number and self.smartflo_did_number not in self.from_numbers:
            self.from_numbers.append(self.smartflo_did_number)
        self.default_from_number = self.smartflo_did_number or (
            self.from_numbers[0] if self.from_numbers else None
        )

    def validate_config(self) -> bool:
        """Validate if required configuration is available."""
        try:
            api_key = (
                self.click_to_call_api_key
                or self.config.get("api_key")
                or self.config.get("smartflo_api_key")
                or os.getenv("SMARTFLO_CLICK_TO_CALL_API_KEY")
            )
            has_did = bool(
                self.smartflo_did_number
                or self.default_from_number
                or self.from_numbers
                or self.config.get("smartflo_did_number")
                or os.getenv("SMARTFLO_DID_NUMBER")
            )
            return bool(api_key and has_did)
        except Exception:
            return False

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Smartflo Click-to-Call Support API.

        Endpoint: POST {SMARTFLO_API_DOMAIN}/v1/click_to_call_support
        """
        # Resolve credentials with 4-tier fallback
        api_key, did, jwt_token, api_domain = resolve_smartflo_credentials(
            call_details=kwargs,
            org_config=self.config,
        )

        # Smartflo strictly accepts only numeric digits (no '+', '-', or spaces)
        def clean_num(n: Optional[str]) -> str:
            if not n:
                return ""
            return re.sub(r"[^\d]", "", str(n).strip())

        clean_customer = clean_num(to_number)
        clean_did = clean_num(did)
        clean_from = clean_num(from_number)
        
        # Priority for caller_id: use configured DID if present, else cleaned from_number
        caller_id = clean_did if clean_did else clean_from

        # Safe logging - NEVER log credentials or raw tokens
        logger.info(
            f"[Smartflo] Initiating outbound call: run_id={workflow_run_id}, "
            f"to={mask_phone_number(clean_customer)}, caller_id={caller_id}"
        )

        endpoint_url = f"{api_domain}/v1/click_to_call_support"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"

        # Prepare request payload as documented
        payload = {
            "customer_number": clean_customer,
            "caller_id": caller_id,
            "api_key": api_key,
            "async": 1,
            "customer_ring_timeout": 30,
            "custom_identifier": str(workflow_run_id or kwargs.get("agent_id") or ""),
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    endpoint_url,
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as e:
                logger.error(f"[Smartflo] Request timed out connecting to {endpoint_url}")
                raise HTTPException(status_code=504, detail=f"Smartflo API timeout: {e}") from e
            except httpx.RequestError as e:
                logger.error(f"[Smartflo] Network request failed: {e}")
                raise HTTPException(status_code=502, detail=f"Smartflo API network error: {e}") from e

            # If Smartflo rejects caller_id (422), attempt intelligent fallback across standard Indian DID formats
            # e.g., 10-digit (8065254733), 12-digit with 91 (918065254733), or 11-digit with 0 (08065254733)
            if response.status_code == 422 and "caller_id" in response.text:
                candidates = []
                if len(caller_id) == 12 and caller_id.startswith("91"):
                    candidates.append(caller_id[2:])        # 10-digit
                    candidates.append(f"0{caller_id[2:]}")  # 11-digit
                elif len(caller_id) == 10:
                    candidates.append(f"91{caller_id}")     # 12-digit with 91
                    candidates.append(f"0{caller_id}")      # 11-digit with 0
                elif len(caller_id) == 11 and caller_id.startswith("0"):
                    candidates.append(caller_id[1:])        # 10-digit
                    candidates.append(f"91{caller_id[1:]}") # 12-digit

                # Also test clean_from if clean_did was used and differed
                if clean_from and clean_from != caller_id and clean_from not in candidates:
                    candidates.append(clean_from)

                for alt_caller_id in candidates:
                    logger.info(
                        f"[Smartflo] Retrying call with candidate caller_id: {alt_caller_id} (original: {caller_id})"
                    )
                    payload["caller_id"] = alt_caller_id
                    try:
                        retry_resp = await client.post(
                            endpoint_url,
                            json=payload,
                            headers=headers,
                        )
                        if retry_resp.status_code in (200, 201, 202):
                            response = retry_resp
                            caller_id = alt_caller_id
                            logger.info(f"[Smartflo] Call initiated successfully with caller_id={caller_id}")
                            break
                        else:
                            logger.warning(
                                f"[Smartflo] Candidate {alt_caller_id} failed with status {retry_resp.status_code}: {retry_resp.text}"
                            )
                    except Exception as retry_err:
                        logger.warning(f"[Smartflo] Retry with candidate {alt_caller_id} failed: {retry_err}")

        if response.status_code not in (200, 201, 202):
            logger.error(
                f"[Smartflo] API error response status={response.status_code}: {response.text}"
            )
            raise HTTPException(
                status_code=400 if response.status_code == 422 else response.status_code,
                detail=f"Smartflo API call failed with status {response.status_code}: {response.text}",
            )

        try:
            resp_data = response.json()
        except Exception:
            resp_data = {"raw_text": response.text}

        ref_id = resp_data.get("ref_id") or resp_data.get("id") or str(workflow_run_id or "")
        call_id = resp_data.get("call_id") or ref_id
        status = resp_data.get("status", "initiated")

        logger.info(
            f"[Smartflo] Call created: ref_id={ref_id}, call_id={call_id}, status={status}"
        )

        # Cache call state in Redis for WebSocket and callback resolution
        state = {
            "workflow_run_id": workflow_run_id,
            "workflow_id": kwargs.get("workflow_id"),
            "organization_id": kwargs.get("organization_id"),
            "agent_id": kwargs.get("agent_id"),
            "customer_number": to_number,
            "caller_id": caller_id,
            "ref_id": ref_id,
            "call_id": call_id,
            "status": status,
        }
        await save_smartflo_call_state(ref_id, call_id, to_number, state)

        return CallInitiationResult(
            call_id=str(call_id),
            status=status,
            caller_number=caller_id,
            provider_metadata={
                "smartflo_ref_id": ref_id,
                "smartflo_call_id": call_id,
                "call_id": call_id,
                "customer_number": to_number,
                "caller_id": caller_id,
            },
            raw_response=resp_data,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Get call status."""
        return {
            "call_id": call_id,
            "status": "in-progress",
        }

    async def get_available_phone_numbers(self) -> List[str]:
        """List configured Smartflo phone numbers."""
        return self.from_numbers or ([self.default_from_number] if self.default_from_number else [])

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """Smartflo uses API keys or IP allowlisting."""
        return True

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        """Generate response pointing Smartflo Voice Bot to the WebSocket endpoint."""
        from api.utils.common import get_backend_endpoints

        backend_endpoint, _ = await get_backend_endpoints()
        ws_host = backend_endpoint.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_host}/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}"

        return json.dumps({
            "status": "success",
            "url": ws_url,
            "ws_url": ws_url,
        })

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """Fetch call duration and cost."""
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "completed",
            "raw_response": {},
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize Smartflo lifecycle callback events.

        Smartflo events:
        initiated, ringing, answered, connected, completed, failed, busy, no-answer, cancelled
        """
        raw_status = str(
            data.get("status")
            or data.get("event")
            or data.get("call_status")
            or "in-progress"
        ).lower().strip()

        status_mapping = {
            "initiated": TelephonyCallStatus.INITIATED,
            "ringing": TelephonyCallStatus.RINGING,
            "answered": TelephonyCallStatus.ANSWERED,
            "connected": TelephonyCallStatus.IN_PROGRESS,
            "in-progress": TelephonyCallStatus.IN_PROGRESS,
            "in_progress": TelephonyCallStatus.IN_PROGRESS,
            "completed": TelephonyCallStatus.COMPLETED,
            "hangup": TelephonyCallStatus.COMPLETED,
            "ended": TelephonyCallStatus.COMPLETED,
            "failed": TelephonyCallStatus.FAILED,
            "busy": TelephonyCallStatus.BUSY,
            "no-answer": TelephonyCallStatus.NO_ANSWER,
            "no_answer": TelephonyCallStatus.NO_ANSWER,
            "cancelled": TelephonyCallStatus.CANCELED,
            "canceled": TelephonyCallStatus.CANCELED,
        }

        call_id = str(data.get("call_id") or data.get("ref_id") or data.get("id") or "")
        normalized_status = status_mapping.get(raw_status, TelephonyCallStatus.IN_PROGRESS)

        return {
            "call_id": call_id,
            "status": normalized_status.value,
            "from_number": data.get("caller_id") or data.get("from"),
            "to_number": data.get("customer_number") or data.get("to"),
            "duration": data.get("duration") or data.get("call_duration"),
            "extra": data,
        }

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """Check if incoming request is from Smartflo."""
        if "smartflo" in str(headers.get("user-agent", "")).lower():
            return True
        if "smartflo" in webhook_data or "ref_id" in webhook_data:
            return True
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Parse inbound Smartflo webhook."""
        call_id = str(
            webhook_data.get("call_id")
            or webhook_data.get("ref_id")
            or webhook_data.get("id")
            or ""
        )
        return NormalizedInboundData(
            provider="smartflo",
            call_id=call_id,
            from_number=str(webhook_data.get("from") or webhook_data.get("caller_id") or ""),
            to_number=str(webhook_data.get("to") or webhook_data.get("customer_number") or ""),
            direction="inbound",
            call_status="ringing",
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return True

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        return {
            "status": "success",
            "url": websocket_url,
            "ws_url": websocket_url,
        }

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        return ProviderSyncResult(ok=True)

    async def handle_websocket(
        self,
        websocket: WebSocket,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle Smartflo WebSocket connection for audio streaming.

        Exchanges handshake ("connected", "start") if provided by Smartflo,
        and begins bidirectional audio via run_pipeline_telephony.
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        logger.info(
            f"[Smartflo] WebSocket connected for workflow_id={workflow_id}, "
            f"workflow_run_id={workflow_run_id}, org_id={organization_id}"
        )

        stream_sid = f"smartflo_stream_{workflow_run_id}"
        call_sid = f"smartflo_call_{workflow_run_id}"

        # Attempt to inspect initial messages with a short timeout.
        # Smartflo Voice Bot may send {"event": "connected"} and/or {"event": "start"}
        # or immediately stream binary frames.
        try:
            first_msg = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            logger.debug(f"[Smartflo] First WS message: {first_msg}")
            try:
                msg_data = json.loads(first_msg)
                event = msg_data.get("event")
                if event == "connected":
                    # Wait for optional second message ("start")
                    try:
                        second_msg = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                        logger.debug(f"[Smartflo] Second WS message: {second_msg}")
                        msg_data = json.loads(second_msg)
                    except Exception:
                        pass

                start_data = msg_data.get("start") or msg_data
                encoding = "audio/x-mulaw"
                if isinstance(start_data, dict):
                    stream_sid = start_data.get("streamSid") or stream_sid
                    call_sid = start_data.get("callSid") or call_sid
                    media_format = start_data.get("mediaFormat") or {}
                    if isinstance(media_format, dict):
                        encoding = media_format.get("encoding") or encoding
            except Exception as parse_err:
                logger.debug(f"[Smartflo] Handshake JSON parse note: {parse_err}")
        except asyncio.TimeoutError:
            logger.debug(f"[Smartflo] No textual handshake received, proceeding directly")
        except Exception as e:
            logger.debug(f"[Smartflo] Handshake wait note: {e}")

        # Start the pipeline with Smartflo transport
        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_id=call_sid,
            transport_kwargs={
                "stream_sid": stream_sid,
                "call_sid": call_sid,
                "encoding": encoding,
            },
        )

    async def handle_external_websocket(
        self,
        websocket: WebSocket,
        *,
        organization_id: int,
        workflow_id: int,
        workflow_run_id: int,
        params: Dict[str, str],
    ) -> None:
        """Handle agent-stream external WebSocket connection."""
        await self.handle_websocket(
            websocket,
            workflow_id=workflow_id,
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
        )

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """Generate a provider-specific error response."""
        from fastapi import Response

        return (
            Response(
                content=json.dumps({"error": error_type, "message": message}),
                media_type="application/json",
            ),
            "application/json",
        )

    def supports_transfers(self) -> bool:
        """Check if Smartflo supports transfers."""
        return False

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Call transfer implementation."""
        raise NotImplementedError("Call transfer is not currently supported for Smartflo provider")

