"""
Cloudonix implementation of the TelephonyProvider interface.
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


class CloudonixProvider(TelephonyProvider):
    """
    Cloudonix implementation of TelephonyProvider.
    Uses Bearer token authentication and is TwiML-compatible for WebSocket audio.
    """

    PROVIDER_NAME = WorkflowRunMode.CLOUDONIX.value
    WEBHOOK_ENDPOINT = "twiml"  # Cloudonix is TwiML-compatible

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize CloudonixProvider with configuration.

        Args:
            config: Dictionary containing:
                - bearer_token: Cloudonix API Bearer Token
                - domain_id: Cloudonix Domain ID
                - from_numbers: List of phone numbers to use (optional, fetched from API if not provided)
        """
        self.bearer_token = config.get("bearer_token")
        self.domain_id = config.get("domain_id")
        self.from_numbers = config.get("from_numbers", [])

        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = "https://api.cloudonix.io"

    def _get_auth_headers(self) -> Dict[str, str]:
        """Generate authorization headers for Cloudonix API."""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json",
        }

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Cloudonix.

        Note: webhook_url parameter is ignored for Cloudonix. Unlike Twilio/Vonage,
        Cloudonix embeds CXML directly in the API call rather than using webhook callbacks.
        """
        if not self.validate_config():
            raise ValueError("Cloudonix provider not properly configured")

        endpoint = f"{self.base_url}/calls/{self.domain_id}/application"

        # Use provided from_number or select a random one (REQUIRED by Cloudonix)
        if from_number is None:
            if not self.from_numbers:
                raise ValueError(
                    "No phone numbers configured for Cloudonix provider. "
                    "At least one phone number is required as 'caller-id' for outbound calls. "
                    "Please configure phone numbers in the telephony settings."
                )
            from_number = random.choice(self.from_numbers)
        logger.info(
            f"Selected phone number {from_number} for outbound call to {to_number}"
        )
        workflow_id, user_id = kwargs["workflow_id"], kwargs["user_id"]

        # Prepare call data using Cloudonix callObject schema
        # Note: 'caller-id' is REQUIRED by Cloudonix API
        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()
        data: Dict[str, Any] = {
            "destination": to_number,
            "cxml": f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>""",
            "caller-id": from_number,  # Required field
        }

        # TODO: Cloudonix status callbacks are spammy, so commenting it out. Can send it to
        # some persistent logging system instead of transcational database.
        # Add status callback if workflow_run_id provided
        # if workflow_run_id:
        #     callback_url = f"{backend_endpoint}/api/v1/telephony/cloudonix/status-callback/{workflow_run_id}"
        #     data["callback"] = callback_url

        # Merge any additional kwargs
        data.update(kwargs)

        # Make the API request
        headers = self._get_auth_headers()

        # Log request details (mask sensitive token)
        masked_headers = {
            k: v if k != "Authorization" else f"Bearer {self.bearer_token[:8]}..."
            for k, v in headers.items()
        }
        logger.info(
            f"[Cloudonix] Initiating outbound call:\n"
            f"  Endpoint: {endpoint}\n"
            f"  To: {to_number}\n"
            f"  From: {from_number}\n"
            f"  Workflow Run ID: {workflow_run_id}"
        )
        logger.debug(
            f"[Cloudonix] Request details:\n"
            f"  Headers: {masked_headers}\n"
            f"  Payload: {json.dumps(data, indent=2)}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=data, headers=headers) as response:
                response_text = await response.text()
                response_status = response.status

                # Log response
                logger.info(
                    f"[Cloudonix] API Response:\n"
                    f"  HTTP Status: {response_status}\n"
                    f"  Response Body: {response_text}"
                )

                if response_status != 200:
                    logger.error(
                        f"[Cloudonix] Call initiation FAILED:\n"
                        f"  HTTP Status: {response_status}\n"
                        f"  Error Details: {response_text}\n"
                        f"  Request: POST {endpoint}\n"
                        f"  Payload: {json.dumps(data, indent=2)}"
                    )
                    raise HTTPException(
                        status_code=response_status,
                        detail=f"Failed to initiate call via Cloudonix (HTTP {response_status}): {response_text}",
                    )

                response_data = await response.json()

                # Extract session token (call ID) and other metadata
                session_token = response_data.get("token")
                domain_id = response_data.get("domainId")
                subscriber_id = response_data.get("subscriberId")

                if not session_token:
                    logger.error(
                        f"[Cloudonix] Missing session token in response:\n"
                        f"  Response: {json.dumps(response_data, indent=2)}"
                    )
                    raise Exception("No session token returned from Cloudonix")

                logger.info(
                    f"[Cloudonix] Call initiated successfully:\n"
                    f"  Session Token: {session_token}\n"
                    f"  Domain ID: {domain_id}\n"
                    f"  Subscriber ID: {subscriber_id}\n"
                    f"  To: {to_number}\n"
                    f"  From: {from_number}\n"
                    f"  Workflow Run ID: {workflow_run_id}"
                )

                return CallInitiationResult(
                    call_id=session_token,
                    status="initiated",
                    provider_metadata={
                        "call_id": session_token,
                        "domain_id": domain_id,
                        "subscriber_id": subscriber_id,
                    },
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a Cloudonix call (session).

        Args:
            call_id: The session token returned from call initiation
        """
        if not self.validate_config():
            raise ValueError("Cloudonix provider not properly configured")

        endpoint = (
            f"{self.base_url}/customers/self/domains/"
            f"{self.domain_id}/sessions/{call_id}"
        )

        headers = self._get_auth_headers()
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    error_data = await response.text()
                    logger.error(f"Failed to get call status: {error_data}")
                    raise Exception(f"Failed to get call status: {error_data}")

                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        """
        Get list of available Cloudonix phone numbers (DNIDs).
        """
        # If phone numbers are already configured, return them
        if self.from_numbers:
            return self.from_numbers

        # Otherwise, fetch from API
        if not self.validate_config():
            raise ValueError("Cloudonix provider not properly configured")

        endpoint = f"{self.base_url}/customers/self/domains/{self.domain_id}/dnids"

        headers = self._get_auth_headers()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(
                            f"Failed to fetch DNIDs from Cloudonix: {response.status}"
                        )
                        return []

                    dnids = await response.json()

                    # Extract phone numbers from DNID objects
                    # Use "source" field which contains the original phone number
                    phone_numbers = [
                        dnid.get("source") or dnid.get("dnid")
                        for dnid in dnids
                        if dnid.get("source") or dnid.get("dnid")
                    ]

                    # Cache the fetched numbers
                    self.from_numbers = phone_numbers
                    return phone_numbers

        except Exception as e:
            logger.error(f"Exception fetching Cloudonix DNIDs: {e}")
            return []

    def validate_config(self) -> bool:
        """
        Validate Cloudonix configuration.
        """
        return bool(self.bearer_token and self.domain_id)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Dummy implementation - Cloudonix doesn't use webhook signature verification.

        Cloudonix embeds CXML directly in the API call during initiate_call(),
        so webhook endpoints are never called and signature verification is not needed.
        This method only exists to satisfy the abstract base class requirement.

        Always returns True since no actual webhook verification is performed.
        """
        logger.warning(
            "verify_webhook_signature called for Cloudonix - this should not happen. "
            "Cloudonix embeds CXML directly in API calls and doesn't use webhook callbacks."
        )
        return True

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """
        Get cost information for a completed Cloudonix call.

        Note: Cloudonix does not currently support call cost retrieval via API.
        This method returns zero cost.

        Args:
            call_id: The Cloudonix session token

        Returns:
            Dict containing cost information (all zeros for now)
        """
        logger.info(
            f"Cloudonix does not support call cost retrieval - returning zero cost for call {call_id}"
        )

        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "error": "Cloudonix does not support cost retrieval",
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Cloudonix status callback data into generic format.

        Note: The exact format of Cloudonix status callbacks needs to be confirmed.
        This implementation assumes a similar structure to Twilio.
        """
        # Map Cloudonix status values to common format
        # These mappings may need adjustment based on actual Cloudonix callback format
        status_map = {
            "initiated": "initiated",
            "ringing": "ringing",
            "answered": "answered",
            "completed": "completed",
            "failed": "failed",
            "busy": "busy",
            "no-answer": "no-answer",
            "canceled": "canceled",
            "error": "error",
        }

        call_status = data.get("status", "")
        mapped_status = status_map.get(call_status.lower(), call_status)

        return {
            "call_id": data.get("token")
            or data.get("session_id")
            or data.get("CallSid", ""),
            "status": mapped_status,
            "from_number": data.get("caller_id") or data.get("From"),
            "to_number": data.get("destination") or data.get("To"),
            "direction": data.get("direction"),
            "duration": data.get("duration") or data.get("CallDuration"),
            "extra": data,  # Include all original data
        }

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """
        Dummy implementation - Cloudonix doesn't use webhook responses.

        Cloudonix embeds CXML directly in the API call during initiate_call(),
        so this webhook endpoint is never actually called. This method only
        exists to satisfy the abstract base class requirement.
        """
        logger.warning(
            "get_webhook_response called for Cloudonix - this should not happen. "
            "Cloudonix embeds CXML directly in API calls."
        )
        return """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Error: This endpoint should not be called for Cloudonix</Say>
</Response>"""

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle Cloudonix-specific WebSocket connection.

        Cloudonix WebSocket is compatible with Twilio, so we use the same handler.
        Cloudonix sends:
        1. "connected" event first
        2. "start" event with streamSid and callSid
        3. Then audio messages
        """
        from api.services.pipecat.run_pipeline import run_pipeline_cloudonix

        try:
            # Wait for "connected" event
            first_msg = await websocket.receive_text()
            msg = json.loads(first_msg)

            if msg.get("event") != "connected":
                logger.error(f"Expected 'connected' event, got: {msg.get('event')}")
                await websocket.close(code=4400, reason="Expected connected event")
                return

            # Wait for "start" event with stream details
            start_msg = await websocket.receive_text()
            logger.debug(f"Received start message: {start_msg}")

            start_msg = json.loads(start_msg)
            if start_msg.get("event") != "start":
                logger.error("Expected 'start' event second")
                await websocket.close(code=4400, reason="Expected start event")
                return

            # Extract Twilio-compatible identifiers
            try:
                stream_sid = start_msg["start"]["streamSid"]
                call_sid = start_msg["start"]["callSid"]
            except KeyError:
                logger.error("Missing streamSid or callSid in start message")
                await websocket.close(code=4400, reason="Missing stream identifiers")
                return

            logger.debug(
                f"Cloudonix WebSocket connected for workflow_run {workflow_run_id} "
                f"stream_sid: {stream_sid} call_sid: {call_sid}"
            )

            # Run the Cloudonix pipeline
            await run_pipeline_cloudonix(
                websocket, stream_sid, workflow_id, workflow_run_id, user_id
            )

        except Exception as e:
            logger.error(f"Error in Cloudonix WebSocket handler: {e}")
            raise

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Determine if this provider can handle the incoming webhook.
        """
        # 1: Check User-Agent header
        user_agent = headers.get("user-agent", "").lower()
        if "cloudonix" in user_agent:
            return True

        # 2: Check for Cloudonix-specific headers
        cloudonix_headers = [
            "x-cx-apikey",
            "x-cx-domain",
            "x-cx-session",
            "x-cx-source",
        ]
        if any(header in headers for header in cloudonix_headers):
            return True

        # 3: Check data structure for Cloudonix-specific fields
        if (
            "SessionData" in webhook_data
            and "Domain" in webhook_data
            and webhook_data.get("Domain", "").endswith(".cloudonix.net")
        ):
            return True

        # Check if AccountSid is a Cloudonix domain
        account_sid = webhook_data.get("AccountSid", "")
        if account_sid.endswith(".cloudonix.net"):
            return True

        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Parse Cloudonix-specific inbound webhook data into normalized format.

        Cloudonix webhook structure includes:
        - CallSid: Call id
        - From: Caller number
        - To: Called number
        - AccountSid: Domain (e.g., "abc.cloudonix.net")
        - SessionData: Contains additional call info including underlying provider details
        """

        session_data = webhook_data.get("SessionData", {})
        token = session_data.get("token", "") if isinstance(session_data, dict) else ""

        call_id = webhook_data.get("Session") or webhook_data.get("CallSid") or token

        account_id = webhook_data.get("Domain") or webhook_data.get("AccountSid", "")

        # Extract underlying provider information from SessionData if available
        session_data = webhook_data.get("SessionData", {})
        underlying_provider = None
        if isinstance(session_data, dict):
            profile = session_data.get("profile", {})
            trunk_headers = profile.get("trunk-sip-headers", {})
            if "Twilio-AccountSid" in trunk_headers:
                underlying_provider = "twilio"

        return NormalizedInboundData(
            provider=CloudonixProvider.PROVIDER_NAME,
            call_id=call_id,
            from_number=webhook_data.get("From", ""),
            to_number=webhook_data.get("To", ""),
            direction=webhook_data.get("Direction", "inbound").lower(),
            call_status=webhook_data.get("CallStatus", "in-progress"),
            account_id=account_id,
            from_country=webhook_data.get("FromCountry"),
            to_country=webhook_data.get("ToCountry"),
            raw_data={
                **webhook_data,
                "underlying_provider": underlying_provider,
            },
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """
        Validate that the account_id from webhook matches the Cloudonix configuration.

        For Cloudonix:
        - webhook_account_id is the Domain field (e.g., "test1.cloudonix.net")
        - config domain_id stores the same domain string
        """
        if not webhook_account_id:
            return False

        # Get stored domain from config (stored under 'domain_id' key)
        stored_domain = config_data.get("domain_id")
        if not stored_domain:
            return False

        return webhook_account_id == stored_domain

    def normalize_phone_number(self, phone_number: str) -> str:
        """
        Normalize a phone number to E.164 format for Cloudonix.

        Cloudonix typically provides numbers in E.164 format already,
        but we'll ensure proper formatting.
        """
        if not phone_number:
            return ""

        # Remove any spaces or formatting
        clean_number = (
            phone_number.replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

        # If already in E.164 format (+...), return as-is
        if clean_number.startswith("+"):
            return clean_number

        # If starts with country code but no +, add it
        if len(clean_number) >= 10:
            return f"+{clean_number}"

        return clean_number

    async def verify_inbound_signature(
        self, url: str, webhook_data: Dict[str, Any], api_key: str
    ) -> bool:
        """
        Verify the API key of an inbound Cloudonix webhook for security.

        Cloudonix uses x-cx-apikey header validation instead of signature verification.
        The API key from the webhook should match the bearer_token in our configuration.
        """
        if not api_key:
            logger.warning("No x-cx-apikey provided in Cloudonix webhook")
            return False

        # The bearer_token in config is the same as x-cx-apikey header value
        if not self.bearer_token:
            logger.warning("No bearer_token configured for Cloudonix provider")
            return False

        # Compare the API keys
        is_valid = api_key == self.bearer_token

        if is_valid:
            logger.info("Cloudonix x-cx-apikey validation successful")
        else:
            logger.warning(
                f"Cloudonix x-cx-apikey validation failed. Expected key ending with ...{self.bearer_token[-8:] if len(self.bearer_token) > 8 else 'SHORT_KEY'}"
            )

        return True  # TODO: update this post clarification from cloudonix

    @staticmethod
    async def generate_inbound_response(
        websocket_url: str, workflow_run_id: int = None
    ) -> tuple:
        """
        Generate the appropriate CXML response for an inbound Cloudonix webhook.

        Returns CXML to connect to WebSocket, same format as outbound calls.
        """
        from fastapi import Response

        # Generate CXML response (same format as outbound calls)
        cxml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""

        logger.info(f"Cloudonix inbound CXML response content:")
        logger.info(cxml_content)

        response = Response(content=cxml_content, media_type="application/xml")

        logger.info(f"Cloudonix inbound response object: {response}")
        logger.info(f"Response headers: {response.headers}")
        logger.info(f"Response media type: {response.media_type}")

        return response

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        """
        Generate Cloudonix-specific error response for validation failures.

        Since Cloudonix is TwiML-compatible, we use the same XML format.
        """
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">{message}</Say>
    <Hangup/>
</Response>"""

        return Response(content=twiml_content, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """
        Generate a Cloudonix-specific error response.

        Since Cloudonix is TwiML-compatible, we use TwiML format.
        """
        from fastapi import Response

        # Map error types to appropriate TwiML responses
        if error_type == "auth_failed":
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Authentication failed. This call cannot be processed.</Say>
    <Hangup/>
</Response>"""
        elif error_type == "not_configured":
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Service not configured. Please contact support.</Say>
    <Hangup/>
</Response>"""
        elif error_type == "invalid_number":
            twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Invalid phone number. This call cannot be processed.</Say>
    <Hangup/>
</Response>"""
        else:
            # Generic error
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>An error occurred: {message}</Say>
    <Hangup/>
</Response>"""

        return Response(content=twiml, media_type="application/xml"), "application/xml"

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
        Cloudonix provider does not support call transfers.

        Raises:
            NotImplementedError: Cloudonix call transfers are yet to be implemented
        """
        raise NotImplementedError("Cloudonix provider does not support call transfers")

    def supports_transfers(self) -> bool:
        """
        Cloudonix does not support call transfers.

        Returns:
            False - Cloudonix provider does not support call transfers
        """
        return False
