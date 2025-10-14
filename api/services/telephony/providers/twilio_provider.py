"""
Twilio implementation of the TelephonyProvider interface.
"""
import random
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger
from twilio.request_validator import RequestValidator

from api.services.telephony.base import TelephonyProvider
from api.utils.tunnel import TunnelURLProvider


class TwilioProvider(TelephonyProvider):
    """
    Twilio implementation of TelephonyProvider.
    Accepts configuration and works the same regardless of OSS/SaaS mode.
    """

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
    ) -> Dict[str, Any]:
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
            callback_url = f"https://{backend_endpoint}/api/v1/telephony/status-callback/{workflow_run_id}"
            data.update({
                "StatusCallback": callback_url,
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                "StatusCallbackMethod": "POST"
            })
        
        # Add any additional kwargs
        data.update(kwargs)
        
        # Make the API request
        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
            async with session.post(endpoint, data=data, auth=auth) as response:
                if response.status != 201:
                    error_data = await response.json()
                    raise Exception(f"Failed to initiate call: {error_data}")
                
                return await response.json()

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