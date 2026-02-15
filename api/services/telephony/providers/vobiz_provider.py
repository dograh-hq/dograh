"""
Vobiz implementation of the TelephonyProvider interface.
"""

import json
import random
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
        from_number: Optional[str] = None,
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

        # Use provided from_number or select a random one
        if from_number is None:
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
            backend_endpoint, _ = await get_backend_endpoints()
            hangup_url = f"{backend_endpoint}/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}"
            ring_url = f"{backend_endpoint}/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}"
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
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to initiate Vobiz call: {error_data}",
                    )

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
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Vobiz API response missing call identifier. Response: {response_data}"
                        f"Vobiz API response missing call identifier. Response: {response_data}",
                    )

                logger.info(f"Vobiz call initiated successfully. Call ID: {call_id}")

                return CallInitiationResult(
                    call_id=call_id,
                    status="queued",  # Vobiz returns "message": "call fired"
                    provider_metadata={"call_id": call_id},
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
        self,
        url: str,
        params: Dict[str, Any],
        signature: str,
        timestamp: str = None,
        body: str = "",
    ) -> bool:
        """
        Verify Vobiz webhook signature for security.

        Vobiz uses HMAC-SHA256 signature verification with timestamp validation:
        - Header: x-vobiz-signature (HMAC-SHA256 hash)
        - Header: x-vobiz-timestamp (timestamp for replay protection)
        - Signature = HMAC-SHA256(auth_token, timestamp + '.' + body)
        """
        import hashlib
        import hmac
        from datetime import datetime, timezone

        if not signature or not timestamp:
            logger.warning("Missing signature or timestamp headers for Vobiz webhook")
            return False

        if not self.auth_token:
            logger.error(
                "No auth_token available for Vobiz webhook signature verification"
            )
            return False

        try:
            # 1. Timestamp validation (within 5 minutes)
            webhook_timestamp = int(timestamp)
            current_timestamp = int(datetime.now(timezone.utc).timestamp())
            time_diff = abs(current_timestamp - webhook_timestamp)

            if time_diff > 300:  # 5 minutes = 300 seconds
                logger.warning(f"Vobiz webhook timestamp too old: {time_diff}s > 300s")
                return False

            # 2. Signature verification
            # Create expected signature: HMAC-SHA256(auth_token, timestamp + '.' + body)
            payload = f"{timestamp}.{body}"
            expected_signature = hmac.new(
                self.auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            # 3. Compare signatures (timing-safe comparison)
            is_valid = hmac.compare_digest(expected_signature, signature)

            if not is_valid:
                logger.warning(
                    f"Vobiz webhook signature mismatch. Expected: {expected_signature[:8]}..., Got: {signature[:8]}..."
                )

            return is_valid

        except Exception as e:
            logger.error(f"Error verifying Vobiz webhook signature: {e}")
            return False

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
        _, wss_backend_endpoint = await get_backend_endpoints()

        vobiz_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}</Stream>
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

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Determine if this provider can handle the incoming webhook.
        Vobiz webhooks contain CallUUID field.
        """
        return "CallUUID" in webhook_data

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Parse Vobiz-specific inbound webhook data into normalized format.
        """
        return NormalizedInboundData(
            provider=VobizProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallUUID", ""),
            from_number=VobizProvider.normalize_phone_number(
                webhook_data.get("From", "")
            ),
            to_number=VobizProvider.normalize_phone_number(webhook_data.get("To", "")),
            direction=webhook_data.get("Direction", ""),
            call_status=webhook_data.get("CallStatus", ""),
            account_id=webhook_data.get("ParentAuthID"),
            from_country=None,  # Vobiz doesn't provide country information
            to_country=None,  # Vobiz doesn't provide country information
            raw_data=webhook_data,
        )

    @staticmethod
    def normalize_phone_number(phone_number: str) -> str:
        """
        Normalize a phone number to E.164 format for Vobiz.
        Vobiz sends numbers in various formats - normalize to E.164 with +.
        """
        if not phone_number:
            return ""

        # Remove any existing + prefix
        clean_number = phone_number.lstrip("+")

        # If it starts with 1 and has 11 digits, it's a US number
        if clean_number.startswith("1") and len(clean_number) == 11:
            return f"+{clean_number}"
        elif len(clean_number) == 10:
            # Assume US number if 10 digits
            return f"+1{clean_number}"
        elif len(clean_number) > 10:
            # International number without country code detection
            return f"+{clean_number}"

        return phone_number

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """Validate Vobiz auth_id from webhook matches configuration"""
        if not webhook_account_id:
            return False

        stored_auth_id = config_data.get("auth_id")
        return stored_auth_id == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        signature: str,
        timestamp: str = None,
        body: str = "",
    ) -> bool:
        """
        Verify the signature of an inbound Vobiz webhook for security.
        Uses the same HMAC-SHA256 verification as other Vobiz webhooks.
        """
        return await self.verify_webhook_signature(
            url, webhook_data, signature, timestamp, body
        )

    @staticmethod
    async def generate_inbound_response(
        websocket_url: str, workflow_run_id: int = None
    ) -> tuple:
        """
        Generate Vobiz XML response for an inbound webhook.

        Note: For hangup callbacks, configure the hangup_url manually in Vobiz dashboard
        to point to: /api/v1/telephony/vobiz/hangup-callback/workflow/{workflow_id}
        """
        from fastapi import Response

        vobiz_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{websocket_url}</Stream>
</Response>"""

        return Response(content=vobiz_xml, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """
        Generate a Vobiz-specific error response.
        """
        from fastapi import Response

        # Vobiz error responses should be valid XML like Plivo
        vobiz_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak voice="WOMAN">Sorry, there was an error processing your call. {message}</Speak>
    <Hangup/>
</Response>"""

        return Response(content=vobiz_xml, media_type="application/xml")

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        """
        Generate Vobiz-specific error response for validation failures with organizational debugging info.
        """
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        vobiz_xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak voice="WOMAN">{message}</Speak>
    <Hangup/>
</Response>"""

        return Response(content=vobiz_xml_content, media_type="application/xml")

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
        Vobiz provider does not support call transfers.

        Raises:
            NotImplementedError: Vobiz call transfers are yet to be implemented
        """
        raise NotImplementedError("Vobiz provider does not support call transfers")

    def supports_transfers(self) -> bool:
        """
        Vobiz does not support call transfers.

        Returns:
            False - Vobiz provider does not support call transfers
        """
        return False
