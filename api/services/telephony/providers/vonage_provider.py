"""
Vonage (Nexmo) implementation of the TelephonyProvider interface.
"""
import json
import random
import time
from typing import Any, Dict, List, Optional

import aiohttp
import jwt
from loguru import logger

from api.services.telephony.base import TelephonyProvider
from api.utils.tunnel import TunnelURLProvider


class VonageProvider(TelephonyProvider):
    """
    Vonage implementation of TelephonyProvider.
    Uses JWT authentication and NCCO for call control.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize VonageProvider with configuration.
        
        Args:
            config: Dictionary containing:
                - api_key: Vonage API Key
                - api_secret: Vonage API Secret
                - application_id: Vonage Application ID
                - private_key: Private key for JWT generation
                - from_numbers: List of phone numbers to use
        """
        self.api_key = config.get("api_key")
        self.api_secret = config.get("api_secret")
        self.application_id = config.get("application_id")
        self.private_key = config.get("private_key")
        self.from_numbers = config.get("from_numbers", [])
        
        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]
        
        self.base_url = "https://api.nexmo.com"

    def _generate_jwt(self) -> str:
        """Generate JWT token for Vonage API authentication."""
        if not self.application_id or not self.private_key:
            raise ValueError("Application ID and private key required for JWT generation")
        
        claims = {
            "application_id": self.application_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,  # 1 hour expiry
            "jti": str(time.time())  # Unique token ID
        }
        
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Initiate an outbound call via Vonage Voice API.
        """
        if not self.validate_config():
            raise ValueError("Vonage provider not properly configured")
        
        endpoint = f"{self.base_url}/v1/calls"
        
        # Select a random phone number
        from_number = random.choice(self.from_numbers)
        # Remove + prefix for Vonage
        from_number = from_number.replace("+", "")
        to_number = to_number.replace("+", "")
        
        logger.info(f"Selected phone number {from_number} for outbound call")
        
        # Prepare call data
        data = {
            "to": [{
                "type": "phone",
                "number": to_number
            }],
            "from": {
                "type": "phone",
                "number": from_number
            },
            "answer_url": [webhook_url],
            "answer_method": "GET"
        }
        
        # Add event webhook if workflow_run_id provided
        if workflow_run_id:
            backend_endpoint = await TunnelURLProvider.get_tunnel_url()
            event_url = f"https://{backend_endpoint}/api/v1/telephony/events/{workflow_run_id}"
            data.update({
                "event_url": [event_url],
                "event_method": "POST"
            })
        
        # Add any additional kwargs
        data.update(kwargs)
        
        # Generate JWT token
        token = self._generate_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Make the API request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, 
                json=data,  # Use json parameter for proper encoding
                headers=headers
            ) as response:
                response_data = await response.json()
                
                if response.status != 201:
                    raise Exception(f"Failed to initiate call: {response_data}")
                
                return response_data

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a Vonage call.
        """
        if not self.validate_config():
            raise ValueError("Vonage provider not properly configured")
        
        endpoint = f"{self.base_url}/v1/calls/{call_id}"
        
        # Generate JWT token
        token = self._generate_jwt()
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(f"Failed to get call status: {error_data}")
                
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        """
        Get list of available Vonage phone numbers.
        """
        return self.from_numbers

    def validate_config(self) -> bool:
        """
        Validate Vonage configuration.
        """
        return bool(
            self.application_id and 
            self.private_key and 
            self.from_numbers
        )

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify Vonage webhook signature for security.
        Vonage uses JWT for webhook signatures.
        """
        if not self.api_secret:
            logger.error("No API secret available for webhook signature verification")
            return False
        
        try:
            # Vonage sends JWT in Authorization header
            # Verify the JWT signature
            decoded = jwt.decode(
                signature, 
                self.api_secret, 
                algorithms=["HS256"],
                options={"verify_signature": True}
            )
            return True
        except jwt.InvalidTokenError:
            return False

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate NCCO response for starting a call session.
        NCCO (Nexmo Call Control Objects) is JSON-based, unlike TwiML which is XML.
        """
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()
        
        # NCCO for WebSocket connection
        ncco = [
            {
                "action": "connect",
                "endpoint": [{
                    "type": "websocket",
                    "uri": f"wss://{backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}",
                    "content-type": "audio/l16;rate=16000",  # 16kHz Linear PCM
                    "headers": {}
                }]
            }
        ]
        
        # Return JSON instead of XML
        return json.dumps(ncco)

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """
        Get cost information for a completed Vonage call.
        
        Args:
            call_id: The Vonage Call UUID
            
        Returns:
            Dict containing cost information
        """
        headers = self._get_auth_headers()
        endpoint = f"https://api.nexmo.com/v1/calls/{call_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status != 200:
                        error_data = await response.json()
                        logger.error(f"Failed to get Vonage call cost: {error_data}")
                        return {
                            "cost_usd": 0.0,
                            "duration": 0,
                            "status": "error",
                            "error": str(error_data)
                        }
                    
                    call_data = await response.json()
                    
                    # Vonage returns price and rate
                    # Price is the total cost, rate is the per-minute rate
                    price = float(call_data.get("price", 0))
                    cost_usd = price  # Vonage returns positive values
                    
                    # Duration is in seconds
                    duration = int(call_data.get("duration", 0))
                    
                    # Get the call status
                    status = call_data.get("status", "unknown")
                    
                    return {
                        "cost_usd": cost_usd,
                        "duration": duration,
                        "status": status,
                        "price_unit": "USD",  # Vonage uses USD by default
                        "rate": call_data.get("rate", 0),  # Per-minute rate
                        "raw_response": call_data
                    }
                    
        except Exception as e:
            logger.error(f"Exception fetching Vonage call cost: {e}")
            return {
                "cost_usd": 0.0,
                "duration": 0,
                "status": "error",
                "error": str(e)
            }