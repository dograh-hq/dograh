import hashlib
import json
import os
from typing import Any, Dict

import httpx
from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel

from api.db import db_client

NANGO_ALLOWED_INTEGRATIONS = [
    i.strip() for i in os.environ.get("NANGO_ALLOWED_INTEGRATIONS", "slack").split(",")
]


class NangoWebhookRequest(BaseModel):
    type: str
    connectionId: str
    providerConfigKey: str
    authMode: str
    provider: str
    environment: str
    operation: str
    endUser: dict  # Contains endUserId and organizationId
    success: bool


class NangoService:
    def __init__(self):
        self.base_url = "https://api.nango.dev"
        self.secret_key = os.getenv("NANGO_API_KEY")

    def _verify_webhook_signature(
        self, request_body: str, signature: str = None
    ) -> bool:
        """
        Verify the webhook signature using SHA256 hash.

        Args:
            request_body: The raw request body as string
            signature: The signature from request headers (optional for now)

        Returns:
            True if signature is valid
        """
        expected_signature = self.secret_key + request_body
        expected_hash = hashlib.sha256(expected_signature.encode("utf-8")).hexdigest()
        return expected_hash == signature

    async def create_session(
        self, user_id: str, organization_id: int
    ) -> Dict[str, Any]:
        """
        Create a Nango session for the given user and organization.

        Args:
            user_id: The end user ID
            organization_id: The organization ID

        Returns:
            Response from Nango API
        """
        if not self.secret_key:
            raise ValueError("NANGO_SECRET_KEY environment variable is not set")

        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "end_user": {"id": user_id},
            "organization": {"id": str(organization_id)},
            "allowed_integrations": NANGO_ALLOWED_INTEGRATIONS,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/connect/sessions", headers=headers, json=payload
            )

            if response.status_code != 201:
                raise httpx.HTTPStatusError(
                    f"Nango API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )

            return response.json()

    async def process_webhook(
        self, raw_body: bytes, signature: str = None
    ) -> Dict[str, str]:
        """
        Process incoming Nango webhook request.

        Args:
            raw_body: The raw request body as bytes
            signature: Optional signature from request headers

        Returns:
            Dict with status and message
        """
        # Decode and parse the request body
        try:
            body_text = raw_body.decode("utf-8")
            webhook_json = json.loads(body_text) if body_text else {}
            logger.debug(f"received webhook from nango: {webhook_json}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e} body_text: {body_text}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

        # Verify webhook signature
        if not self._verify_webhook_signature(body_text, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # Parse webhook data
        try:
            webhook_data = NangoWebhookRequest(**webhook_json)
        except Exception as e:
            logger.error(f"Failed to parse webhook data: {e}")
            raise HTTPException(
                status_code=400, detail=f"Invalid webhook format: {str(e)}"
            )

        # Extract user and organization IDs from the webhook payload
        end_user = webhook_data.endUser
        if (
            not end_user
            or "endUserId" not in end_user
            or "organizationId" not in end_user
        ):
            raise HTTPException(
                status_code=400, detail="Missing endUser information in webhook payload"
            )

        user_id = int(end_user["endUserId"])
        organization_id = int(end_user["organizationId"])

        # Use the connectionId as the integration_id since it's unique per integration
        integration_id = webhook_data.connectionId

        # Initialize connection_details
        connection_details = {}

        # Fetch connection details if type is auth and provider is slack
        if webhook_data.type == "auth":
            connection_details = await self._fetch_connection_details(
                integration_id, webhook_data.provider
            )

        # Create the integration in the database
        integration = await db_client.create_integration(
            integration_id=integration_id,
            organization_id=organization_id,
            provider=webhook_data.provider,
            created_by=user_id,
            is_active=True,
            connection_details=connection_details,
        )

        return {
            "status": "success",
            "message": f"Integration created successfully with ID: {integration.id}",
        }

    async def _fetch_connection_details(
        self, connection_id: str, provider_key: str
    ) -> Dict[str, Any]:
        """
        Fetch connection details from Nango API for a given connection ID.

        Args:
            connection_id: The connection ID from the webhook

        Returns:
            Connection details as a dictionary
        """

        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/connection/{connection_id}/?provider_config_key={provider_key}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    logger.error(
                        f"Failed to fetch connection details: {response.status_code} - {response.text}"
                    )
                    raise httpx.HTTPStatusError(
                        f"Nango API error while fetching connection: {response.status_code}",
                        request=response.request,
                        response=response,
                    )

                connection_details = response.json()
                return connection_details

            except httpx.HTTPError as e:
                logger.error(f"HTTP error while fetching connection details: {e}")
                # Return empty dict if API call fails, but log the error
                return {}

    async def get_access_token(
        self, connection_id: str, provider_config_key: str
    ) -> Dict[str, Any]:
        """
        Get the latest access token for a connection from Nango.

        Args:
            connection_id: The connection ID
            provider_config_key: The provider config key (e.g., 'google-sheet')

        Returns:
            Dict containing access token and other connection details
        """
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/connection/{connection_id}?provider_config_key={provider_config_key}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    logger.error(
                        f"Failed to get access token: {response.status_code} - {response.text}"
                    )
                    raise httpx.HTTPStatusError(
                        f"Nango API error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )

                return response.json()

            except httpx.HTTPError as e:
                logger.error(f"HTTP error while getting access token: {e}")
                raise


# Create a singleton instance
nango_service = NangoService()
