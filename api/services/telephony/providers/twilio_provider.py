"""
Twilio implementation of the TelephonyProvider interface.
"""

import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger
from twilio.request_validator import RequestValidator

from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints

if TYPE_CHECKING:
    from fastapi import WebSocket


class TwilioProvider(TelephonyProvider):
    """
    Twilio implementation of TelephonyProvider.
    Accepts configuration and works the same regardless of OSS/SaaS mode.
    """

    PROVIDER_NAME = WorkflowRunMode.TWILIO.value
    WEBHOOK_ENDPOINT = "twiml"

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize TwilioProvider with configuration.

        Args:
            config: Dictionary containing:
                - account_sid: Twilio Account SID
                - auth_token: Twilio Auth Token
                - from_numbers: List of phone numbers to use
        """
        self.account_sid = config.get("account_sid")
        self.auth_token = config.get("auth_token")
        self.from_numbers = config.get("from_numbers", [])

        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Twilio.
        """
        if not self.validate_config():
            raise ValueError("Twilio provider not properly configured")

        endpoint = f"{self.base_url}/Calls.json"

        # Use provided from_number or select a random one
        if from_number is None:
            from_number = random.choice(self.from_numbers)
        logger.info(f"Selected phone number {from_number} for outbound call")
        logger.info(f"Webhook url received - {webhook_url}")

        # Prepare call data
        data = {"To": to_number, "From": from_number, "Url": webhook_url}

        # Add status callback if workflow_run_id provided
        if workflow_run_id:
            backend_endpoint, _ = await get_backend_endpoints()
            callback_url = f"{backend_endpoint}/api/v1/telephony/twilio/status-callback/{workflow_run_id}"
            data.update(
                {
                    "StatusCallback": callback_url,
                    "StatusCallbackEvent": [
                        "initiated",
                        "ringing",
                        "answered",
                        "completed",
                    ],
                    "StatusCallbackMethod": "POST",
                }
            )

        data.update(kwargs)

        # Make the API request
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
            async with session.post(endpoint, data=data, auth=auth) as response:
                if response.status != 201:
                    error_data = await response.json()
                    raise HTTPException(
                        status_code=response.status, detail=json.dumps(error_data)
                    )

                response_data = await response.json()

                return CallInitiationResult(
                    call_id=response_data["sid"],
                    status=response_data.get("status", "queued"),
                    provider_metadata={"call_id": response_data["sid"]},
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a Twilio call.
        """
        if not self.validate_config():
            raise ValueError("Twilio provider not properly configured")

        endpoint = f"{self.base_url}/Calls/{call_id}.json"

        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
            async with session.get(endpoint, auth=auth) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(f"Failed to get call status: {error_data}")

                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        """
        Get list of available Twilio phone numbers.
        """
        return self.from_numbers

    def validate_config(self) -> bool:
        """
        Validate Twilio configuration.
        """
        return bool(self.account_sid and self.auth_token and self.from_numbers)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify Twilio webhook signature for security.
        """
        if not self.auth_token:
            logger.error("No auth token available for webhook signature verification")
            return False

        validator = RequestValidator(self.auth_token)
        return validator.validate(url, params, signature)

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate TwiML response for starting a call session.
        """
        _, wss_backend_endpoint = await get_backend_endpoints()

        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""
        logger.info(f"Twiml content generated - {twiml_content}")
        return twiml_content

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """
        Get cost information for a completed Twilio call.

        Args:
            call_id: The Twilio Call SID

        Returns:
            Dict containing cost information
        """
        endpoint = f"{self.base_url}/Calls/{call_id}.json"

        try:
            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
                async with session.get(endpoint, auth=auth) as response:
                    if response.status != 200:
                        error_data = await response.json()
                        logger.error(f"Failed to get Twilio call cost: {error_data}")
                        return {
                            "cost_usd": 0.0,
                            "duration": 0,
                            "status": "error",
                            "error": str(error_data),
                        }

                    call_data = await response.json()

                    # Twilio returns price as a negative string (e.g., "-0.0085")
                    price_str = call_data.get("price", "0")
                    cost_usd = abs(float(price_str)) if price_str else 0.0

                    # Duration is in seconds as a string
                    duration = int(call_data.get("duration", "0"))

                    return {
                        "cost_usd": cost_usd,
                        "duration": duration,
                        "status": call_data.get("status", "unknown"),
                        "price_unit": call_data.get("price_unit", "USD"),
                        "raw_response": call_data,
                    }

        except Exception as e:
            logger.error(f"Exception fetching Twilio call cost: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Twilio status callback data into generic format.
        """
        return {
            "call_id": data.get("CallSid", ""),
            "status": data.get("CallStatus", ""),
            "from_number": data.get("From"),
            "to_number": data.get("To"),
            "direction": data.get("Direction"),
            "duration": data.get("CallDuration") or data.get("Duration"),
            "extra": data,  # Include all original data
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle Twilio-specific WebSocket connection.

        Twilio sends:
        1. "connected" event first
        2. "start" event with streamSid and callSid
        3. Then audio messages
        """
        from api.services.pipecat.run_pipeline import run_pipeline_twilio

        try:
            # Wait for "connected" event
            first_msg = await websocket.receive_text()
            msg = json.loads(first_msg)

            if msg.get("event") != "connected":
                logger.error(f"Expected 'connected' event, got: {msg.get('event')}")
                await websocket.close(code=4400, reason="Expected connected event")
                return

            logger.debug(
                f"Twilio WebSocket connected for workflow_run {workflow_run_id}"
            )

            # Wait for "start" event with stream details
            start_msg = await websocket.receive_text()
            logger.debug(f"Received start message: {start_msg}")

            start_msg = json.loads(start_msg)
            if start_msg.get("event") != "start":
                logger.error("Expected 'start' event second")
                await websocket.close(code=4400, reason="Expected start event")
                return

            # Extract Twilio-specific identifiers
            try:
                stream_sid = start_msg["start"]["streamSid"]
                call_sid = start_msg["start"]["callSid"]
            except KeyError:
                logger.error("Missing streamSid or callSid in start message")
                await websocket.close(code=4400, reason="Missing stream identifiers")
                return

            # Run the Twilio pipeline
            await run_pipeline_twilio(
                websocket, stream_sid, call_sid, workflow_id, workflow_run_id, user_id
            )

        except Exception as e:
            logger.error(f"Error in Twilio WebSocket handler: {e}")
            raise

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Determine if this provider can handle the incoming webhook.

        Twilio webhooks have specific characteristics:
        - User-Agent: "TwilioProxy/1.1"
        - Headers: "x-twilio-signature", "i-twilio-idempotency-token"
        - Data: CallSid + AccountSid (AC prefix) + ApiVersion
        - AccountSid format: starts with "AC" (not a domain)
        """
        # 1: Check for Twilio-specific User-Agent
        user_agent = headers.get("user-agent", "")
        if "twilioproxy" in user_agent.lower() or user_agent.startswith("TwilioProxy"):
            return True

        # 2: Check for Twilio-specific headers
        twilio_headers = [
            "x-twilio-signature",
            "i-twilio-idempotency-token",
            "x-home-region",
        ]
        if any(header in headers for header in twilio_headers):
            return True

        # 3: Check data structure - CallSid + AccountSid with AC prefix + ApiVersion
        if (
            "CallSid" in webhook_data
            and "AccountSid" in webhook_data
            and "ApiVersion" in webhook_data
        ):
            # Ensure AccountSid looks like Twilio (starts with AC, not a domain)
            account_sid = webhook_data.get("AccountSid", "")
            if account_sid.startswith("AC") and not "." in account_sid:
                return True

        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Parse Twilio-specific inbound webhook data into normalized format.
        """
        return NormalizedInboundData(
            provider=TwilioProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallSid", ""),
            from_number=TwilioProvider.normalize_phone_number(
                webhook_data.get("From", "")
            ),
            to_number=TwilioProvider.normalize_phone_number(webhook_data.get("To", "")),
            direction=webhook_data.get("Direction", ""),
            call_status=webhook_data.get("CallStatus", ""),
            account_id=webhook_data.get("AccountSid"),
            from_country=webhook_data.get("FromCountry")
            or webhook_data.get("CallerCountry"),
            to_country=webhook_data.get("ToCountry")
            or webhook_data.get("CalledCountry"),
            raw_data=webhook_data,
        )

    @staticmethod
    def normalize_phone_number(phone_number: str) -> str:
        """
        Normalize a phone number to E.164 format for Twilio.
        Twilio already provides numbers in E.164 format.
        """
        if not phone_number:
            return ""

        # Twilio numbers are already in E.164 format (+1234567890)
        if phone_number.startswith("+"):
            return phone_number

        # If for some reason it doesn't have +, assume US and add +1
        if phone_number.startswith("1") and len(phone_number) == 11:
            return f"+{phone_number}"
        elif len(phone_number) == 10:
            return f"+1{phone_number}"

        return phone_number

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """Validate Twilio account_sid from webhook matches configuration"""
        if not webhook_account_id:
            return False

        stored_account_sid = config_data.get("account_sid")
        return stored_account_sid == webhook_account_id

    async def verify_inbound_signature(
        self, url: str, webhook_data: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify the signature of an inbound Twilio webhook for security.
        """
        return await self.verify_webhook_signature(url, webhook_data, signature)

    @staticmethod
    async def generate_inbound_response(
        websocket_url: str, workflow_run_id: int = None
    ) -> tuple:
        """
        Generate TwiML response for an inbound Twilio webhook.

        Uses the same StatusCallback URL pattern as outbound calls for consistency.
        """
        from fastapi import Response

        # Generate StatusCallback URL using same pattern as outbound calls
        status_callback_attr = ""
        if workflow_run_id:
            backend_endpoint, _ = await get_backend_endpoints()
            status_callback_url = f"{backend_endpoint}/api/v1/telephony/twilio/status-callback/{workflow_run_id}"
            status_callback_attr = f' statusCallback="{status_callback_url}"'

        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}"{status_callback_attr}></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""

        return Response(content=twiml_content, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """
        Generate a Twilio-specific error response.
        """
        from fastapi import Response

        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Sorry, there was an error processing your call. {message}</Say>
    <Hangup/>
</Response>"""

        return Response(content=twiml_content, media_type="application/xml")

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        """
        Generate Twilio-specific error response for validation failures with organizational debugging info.
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
        Initiate a call transfer via Twilio.

        Uses inline TwiML to put the destination into a conference when they answer,
        and a status callback to track the transfer outcome.

        Args:
            destination: The destination phone number (E.164 format)
            transfer_id: Unique identifier for tracking this transfer
            conference_name: Name of the conference to join the destination into
            timeout: Transfer timeout in seconds
            **kwargs: Additional Twilio-specific parameters

        Returns:
            Dict containing transfer result information

        Raises:
            ValueError: If provider configuration is invalid
            Exception: If Twilio API call fails
        """
        if not self.validate_config():
            raise ValueError("Twilio provider not properly configured")

        # Select a random phone number for the transfer
        from_number = random.choice(self.from_numbers)
        logger.info(f"Selected phone number {from_number} for transfer call")

        backend_endpoint, _ = await get_backend_endpoints()

        status_callback_url = (
            f"{backend_endpoint}/api/v1/telephony/transfer-result/{transfer_id}"
        )

        # Inline TwiML: when the destination answers, put them into the conference
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>You have answered a transfer call. Connecting you now.</Say>
    <Dial>
        <Conference endConferenceOnExit="true">{conference_name}</Conference>
    </Dial>
</Response>"""

        # Prepare Twilio API call data
        endpoint = f"{self.base_url}/Calls.json"
        data = {
            "To": destination,
            "From": from_number,
            "Timeout": timeout,
            "Twiml": twiml,
            "StatusCallback": status_callback_url,
            "StatusCallbackEvent": [
                "answered",
                "no-answer",
                "busy",
                "failed",
                "completed",
            ],
            "StatusCallbackMethod": "POST",
        }

        # Add any additional kwargs
        data.update(kwargs)

        try:
            logger.debug(f"Transfer call data: {data}")

            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
                async with session.post(endpoint, data=data, auth=auth) as response:
                    response_status = response.status
                    response_text = await response.text()

                    logger.info(
                        f"Twilio transfer API response status: {response_status}"
                    )
                    logger.debug(f"Twilio transfer API response body: {response_text}")

                    if response_status in [200, 201]:
                        try:
                            response_data = await response.json()
                            call_sid = response_data.get("sid")
                            logger.info(
                                f"Transfer call initiated successfully: {call_sid}"
                            )

                            return {
                                "call_sid": call_sid,
                                "status": response_data.get("status", "queued"),
                                "provider": self.PROVIDER_NAME,
                                "from_number": from_number,
                                "to_number": destination,
                                "raw_response": response_data,
                            }
                        except Exception as e:
                            logger.error(
                                f"Failed to parse Twilio transfer response JSON: {e}"
                            )
                            raise Exception(f"Failed to parse transfer response: {e}")
                    else:
                        error_msg = f"Twilio API call failed with status {response_status}: {response_text}"
                        logger.error(error_msg)
                        raise Exception(error_msg)

        except Exception as e:
            logger.error(f"Exception during Twilio transfer call: {e}")
            raise

    def supports_transfers(self) -> bool:
        """
        Twilio supports call transfers.

        Returns:
            True - Twilio provider supports call transfers
        """
        return True
