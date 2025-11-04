"""
Twilio implementation of the TelephonyProvider interface.
"""
import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from loguru import logger
from twilio.request_validator import RequestValidator

from api.services.telephony.base import CallInitiationResult, TelephonyProvider
from api.utils.tunnel import TunnelURLProvider
from api.enums import WorkflowRunMode

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
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Twilio.
        """
        if not self.validate_config():
            raise ValueError("Twilio provider not properly configured")
        
        endpoint = f"{self.base_url}/Calls.json"
        
        # Select a random phone number
        from_number = random.choice(self.from_numbers)
        logger.info(f"Selected phone number {from_number} for outbound call")
        
        # Prepare call data
        data = {
            "To": to_number,
            "From": from_number,
            "Url": webhook_url
        }
        
        # Add status callback if workflow_run_id provided
        if workflow_run_id:
            backend_endpoint = await TunnelURLProvider.get_tunnel_url()
            callback_url = f"https://{backend_endpoint}/api/v1/telephony/twilio/status-callback/{workflow_run_id}"
            data.update({
                "StatusCallback": callback_url,
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                "StatusCallbackMethod": "POST"
            })
        
        data.update(kwargs)
        
        # Make the API request
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
            async with session.post(endpoint, data=data, auth=auth) as response:
                if response.status != 201:
                    error_data = await response.json()
                    raise Exception(f"Failed to initiate call: {error_data}")
                
                response_data = await response.json()
                
                return CallInitiationResult(
                    call_id=response_data["sid"],
                    status=response_data.get("status", "queued"),
                    provider_metadata={},  # Twilio doesn't need to persist extra data
                    raw_response=response_data
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
        return bool(
            self.account_sid and 
            self.auth_token and 
            self.from_numbers
        )

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
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        
        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""
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
                            "error": str(error_data)
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
                        "raw_response": call_data
                    }
                    
        except Exception as e:
            logger.error(f"Exception fetching Twilio call cost: {e}")
            return {
                "cost_usd": 0.0,
                "duration": 0,
                "status": "error",
                "error": str(e)
            }

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
            "extra": data  # Include all original data
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
            
            logger.debug(f"Twilio WebSocket connected for workflow_run {workflow_run_id}")
            
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