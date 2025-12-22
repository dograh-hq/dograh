"""
Vobiz implementation of the TelephonyProvider interface.
"""

import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from loguru import logger

from api.enums import WorkflowRunMode
from api.services.telephony.base import CallInitiationResult, TelephonyProvider
from api.utils.tunnel import TunnelURLProvider

if TYPE_CHECKING:
    from fastapi import WebSocket


class VobizProvider(TelephonyProvider):
    """
    Vobiz implementation of TelephonyProvider.
    Vobiz uses Plivo-compatible API and WebSocket protocol.
    """

    PROVIDER_NAME = WorkflowRunMode.VOBIZ.value
    WEBHOOK_ENDPOINT = "vobiz-xml"

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize VobizProvider with configuration.

        Args:
            config: Dictionary containing:
                - auth_id: Vobiz Account ID (e.g., MA_SYQRLN1K)
                - auth_token: Vobiz Auth Token
                - from_numbers: List of phone numbers to use (E.164 format without +)
        """
        self.auth_id = config.get("auth_id")
        self.auth_token = config.get("auth_token")
        self.from_numbers = config.get("from_numbers", [])

        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = "https://api.vobiz.ai/api"

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Vobiz.

        Vobiz API differences from Twilio:
        - Uses X-Auth-ID and X-Auth-Token headers instead of Basic Auth
        - Expects JSON body instead of form data
        - Phone numbers in E.164 format WITHOUT + prefix (e.g., 14155551234)
        - Returns "call_uuid" instead of "sid"
        """
        if not self.validate_config():
            raise ValueError("Vobiz provider not properly configured")

        endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/"

        # Select a random phone number
        from_number = random.choice(self.from_numbers)
        logger.info(f"Selected Vobiz phone number {from_number} for outbound call")

        # Remove + prefix if present (Vobiz expects E.164 without +)
        to_number_clean = to_number.lstrip("+")
        from_number_clean = from_number.lstrip("+")

        # Prepare call data (JSON format)
        data = {
            "from": from_number_clean,
            "to": to_number_clean,
            "answer_url": webhook_url,
            "answer_method": "POST",
        }

        # Add hangup callback if workflow_run_id provided
        if workflow_run_id:
            backend_endpoint = await TunnelURLProvider.get_tunnel_url()
            hangup_url = f"https://{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}"
            ring_url = f"https://{backend_endpoint}/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}"
            data.update(
                {
                    "hangup_url": hangup_url,
                    "hangup_method": "POST",
                    "ring_url": ring_url,
                    "ring_method": "POST",
                }
            )

        # Add optional parameters
        data.update(kwargs)

        # Make the API request
        headers = {
            "X-Auth-ID": self.auth_id,
            "X-Auth-Token": self.auth_token,
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=data, headers=headers) as response:
                if response.status != 201:
                    error_data = await response.text()
                    logger.error(f"Vobiz API error: {error_data}")
                    raise Exception(f"Failed to initiate Vobiz call: {error_data}")

                response_data = await response.json()
                logger.info(f"Vobiz API response: {response_data}")

                # Extract call_uuid with multiple fallback options
                call_id = (
                    response_data.get("call_uuid")
                    or response_data.get("CallUUID")
                    or response_data.get("request_uuid")
                    or response_data.get("RequestUUID")
                )

                if not call_id:
                    logger.error(
                        f"No call ID found in Vobiz response. Available keys: {list(response_data.keys())}"
                    )
                    raise Exception(
                        f"Vobiz API response missing call identifier. Response: {response_data}"
                    )

                logger.info(f"Vobiz call initiated successfully. Call ID: {call_id}")

                return CallInitiationResult(
                    call_id=call_id,
                    status="queued",  # Vobiz returns "message": "call fired"
                    provider_metadata={},
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a Vobiz call (CDR).

        Vobiz returns:
        - call_uuid, status, duration, billed_duration
        - call_rate, total_cost (for billing)
        """
        if not self.validate_config():
            raise ValueError("Vobiz provider not properly configured")

        endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/{call_id}/"

        headers = {"X-Auth-ID": self.auth_id, "X-Auth-Token": self.auth_token}

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    error_data = await response.text()
                    logger.error(f"Failed to get Vobiz call status: {error_data}")
                    raise Exception(f"Failed to get call status: {error_data}")

                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        """
        Get list of available Vobiz phone numbers.
        """
        return self.from_numbers

    def validate_config(self) -> bool:
        """
        Validate Vobiz configuration.
        """
        return bool(self.auth_id and self.auth_token and self.from_numbers)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify Vobiz webhook signature for security.

        Vobiz uses Plivo-compatible signature verification (HMAC-SHA256).
        For now, returning True to allow testing.
        TODO: Implement proper signature verification based on Vobiz docs.
        """
        # Plivo/Vobiz signature verification would go here
        # For development, we can skip signature verification
        # In production, implement HMAC-SHA256 verification
        logger.warning("Vobiz webhook signature verification not yet implemented")
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate Vobiz XML response for starting a call session.

        Vobiz uses <Stream> element similar to Twilio but with Plivo-compatible attributes:
        - bidirectional: Enable two-way audio
        - audioTrack: Which audio to stream (inbound, outbound, both)
        - contentType: audio/x-mulaw;rate=8000
        """
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()

        vobiz_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">wss://{backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}</Stream>
</Response>"""
        return vobiz_xml

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """
        Get cost information for a completed Vobiz call.

        Vobiz returns cost in the same CDR endpoint:
        - total_cost: Positive string (e.g., "0.04")
        - call_rate: Per-minute rate (e.g., "0.02")
        - billed_duration: Billable seconds (integer)

        Args:
            call_id: The Vobiz call_uuid

        Returns:
            Dict containing cost information
        """
        endpoint = f"{self.base_url}/v1/Account/{self.auth_id}/Call/{call_id}/"

        try:
            headers = {"X-Auth-ID": self.auth_id, "X-Auth-Token": self.auth_token}

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status != 200:
                        error_data = await response.text()
                        logger.error(f"Failed to get Vobiz call cost: {error_data}")
                        return {
                            "cost_usd": 0.0,
                            "duration": 0,
                            "status": "error",
                            "error": str(error_data),
                        }

                    call_data = await response.json()

                    # Vobiz returns cost as positive string (e.g., "0.04")
                    total_cost_str = call_data.get("total_cost", "0")
                    cost_usd = float(total_cost_str) if total_cost_str else 0.0

                    # Duration is billed_duration in seconds (integer)
                    duration = int(call_data.get("billed_duration", 0))

                    return {
                        "cost_usd": cost_usd,
                        "duration": duration,
                        "status": call_data.get("status", "unknown"),
                        "price_unit": "USD",  # Vobiz always uses USD
                        "call_rate": call_data.get("call_rate", "0"),
                        "raw_response": call_data,
                    }

        except Exception as e:
            logger.error(f"Exception fetching Vobiz call cost: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Vobiz status callback data into generic format.

        Vobiz sends callbacks to hangup_url and ring_url with:
        - call_uuid (instead of CallSid)
        - status, from, to, duration, etc.
        """
        return {
            "call_id": data.get("CallUUID", ""),
            "status": data.get("CallStatus", ""),
            "from_number": data.get("From"),
            "to_number": data.get("To"),
            "direction": data.get("Direction"),
            "duration": data.get("Duration"),
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle Vobiz WebSocket connection using Vobiz WebSocket protocol.

        Extracts stream_id and call_id from the start event and delegates
        message handling to VobizFrameSerializer.
        """
        from api.services.pipecat.run_pipeline import run_pipeline_vobiz

        first_msg = await websocket.receive_text()
        start_msg = json.loads(first_msg)
        logger.debug(f"Received the first message: {start_msg}")

        # Validate that this is a start event
        if start_msg.get("event") != "start":
            logger.error(f"Expected 'start' event, got: {start_msg.get('event')}")
            await websocket.close(code=4400, reason="Expected start event")
            return

        logger.debug(f"Vobiz WebSocket connected for workflow_run {workflow_run_id}")

        try:
            # Extract stream_id and call_id from the start event
            start_data = start_msg.get("start", {})
            stream_id = start_data.get("streamId")
            call_id = start_data.get("callId")

            if not stream_id or not call_id:
                logger.error(f"Missing streamId or callId in start event: {start_data}")
                await websocket.close(code=4400, reason="Missing streamId or callId")
                return

            logger.info(
                f"[run {workflow_run_id}] Starting Vobiz WebSocket handler - "
                f"stream_id: {stream_id}, call_id: {call_id}"
            )

            await run_pipeline_vobiz(
                websocket, stream_id, call_id, workflow_id, workflow_run_id, user_id
            )

            logger.info(f"[run {workflow_run_id}] Vobiz pipeline completed")

        except Exception as e:
            logger.error(
                f"[run {workflow_run_id}] Error in Vobiz WebSocket handler: {e}"
            )
            raise
