# TODO: Remove this file after migrating workflow_run_cost.py to use telephony abstraction
# Deprecated - use api/services/telephony/providers/twilio_provider.py instead

import random
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from loguru import logger
from pydantic import ValidationError
from twilio.request_validator import RequestValidator

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.utils.tunnel import TunnelURLProvider


class TwilioService:
    """Service for interacting with Twilio API."""

    def __init__(self, organization_id: int):
        """Initialize TwilioService with organization_id."""
        self.organization_id = organization_id
        self.account_sid = None
        self.auth_token = None
        self.from_numbers = []
        self.base_url = None

    async def _ensure_credentials(self):
        """Load credentials from organization configuration."""
        if self.account_sid and self.auth_token:
            return

        # Fetch from organization config only - no env var fallback
        config = await db_client.get_configuration(
            self.organization_id,
            OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
        )

        if not config or not config.value:
            raise ValidationError(
                "Twilio credentials not configured for this organization. "
                "Please configure telephony settings."
            )

        self.account_sid = config.value.get("account_sid")
        self.auth_token = config.value.get("auth_token")
        self.from_numbers = config.value.get("from_numbers", [])

        if not self.account_sid or not self.auth_token or not self.from_numbers:
            raise ValidationError(
                "Incomplete Twilio configuration. Please update telephony settings."
            )

        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}"

    async def get_organization_phone_numbers(self) -> List[str]:
        """
        Get the list of Twilio phone numbers configured for the organization.

        Returns:
            List of phone numbers
        """
        await self._ensure_credentials()
        return self.from_numbers

    async def initiate_call(
        self,
        to_number: str,
        url_args: Dict[str, Any] = {},
        workflow_run_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Initiates a Twilio call using the Calls API.

        Args:
            to_number: The destination phone number
            url_args: Dictionary of URL parameters to append to the base URL
            workflow_run_id: The workflow run ID for tracking callbacks
            **kwargs: Additional parameters to pass to the Twilio API

        Returns:
            Dict containing the Twilio API response
        """
        await self._ensure_credentials()

        endpoint = f"{self.base_url}/Calls.json"

        # Get tunnel URL at runtime
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()

        # Construct the URL with parameters if any
        url: str = f"https://{backend_endpoint}/api/v1/twilio/twiml"
        if url_args:
            query_string = urlencode(url_args)
            url = f"{url}?{query_string}"

        logger.debug(f"Initiating call with URL: {url}")

        # Get phone numbers for organization and select one randomly
        phone_numbers = await self.get_organization_phone_numbers()
        from_number = random.choice(phone_numbers)
        logger.info(
            f"Selected phone number {from_number} from {len(phone_numbers)} "
            f"available numbers for org {self.organization_id}"
        )

        # Prepare call data
        data = {"To": to_number, "From": from_number, "Url": url}

        # Add status callback configuration if workflow_run_id is provided
        if workflow_run_id:
            callback_url = f"https://{backend_endpoint}/api/v1/twilio/status-callback/{workflow_run_id}"
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

    async def get_start_call_twiml(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        # Get tunnel URL at runtime
        backend_endpoint = await TunnelURLProvider.get_tunnel_url()

        twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{backend_endpoint}/api/v1/twilio/ws/{workflow_id}/{user_id}/{workflow_run_id}"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""
        return twiml_content

    async def get_call(self, call_sid: str) -> Dict[str, Any]:
        """
        Retrieves information about a specific call.

        Args:
            call_sid: The SID of the call to retrieve

        Returns:
            Dict containing the call information
        """
        await self._ensure_credentials()

        endpoint = f"{self.base_url}/Calls/{call_sid}.json"

        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)
            async with session.get(endpoint, auth=auth) as response:
                if response.status != 200:
                    error_data = await response.json()
                    raise Exception(f"Failed to get call: {error_data}")

                return await response.json()

    async def verify_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify Twilio request signature using official Twilio SDK.

        Args:
            url: The full URL of the webhook
            params: The POST parameters (form data) as a dictionary
            signature: The X-Twilio-Signature header value

        Returns:
            bool: True if signature is valid, False otherwise
        """
        await self._ensure_credentials()

        validator = RequestValidator(self.auth_token)
        return validator.validate(url, params, signature)
