"""
MPS Service Key HTTP Client
This client communicates with the Model Proxy Service (MPS) for service key management.
Service keys are stored and managed entirely in MPS, not in the local database.
"""

from typing import List, Optional

import httpx
from loguru import logger

from api.constants import DEPLOYMENT_MODE, DOGRAH_MPS_SECRET_KEY, MPS_API_URL


class MPSServiceKeyClient:
    """HTTP client for managing service keys via MPS API."""

    def __init__(self):
        self.base_url = MPS_API_URL
        self.timeout = httpx.Timeout(10.0)

    def _get_headers(
        self,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Get headers for MPS API requests.

        Args:
            organization_id: Organization ID for authenticated mode
            created_by: User provider ID for OSS mode

        Returns:
            Dictionary of headers
        """
        headers = {"Content-Type": "application/json"}

        # Add authentication for non-OSS mode
        if DEPLOYMENT_MODE != "oss":
            if DOGRAH_MPS_SECRET_KEY:
                headers["X-Secret-Key"] = DOGRAH_MPS_SECRET_KEY
            if organization_id:
                headers["X-Organization-Id"] = str(organization_id)
        else:
            # OSS mode
            if created_by:
                headers["X-Created-By"] = created_by

        return headers

    async def create_service_key(
        self,
        name: str,
        organization_id: Optional[int] = None,
        created_by: str = None,
        expires_in_days: int = 90,
        description: Optional[str] = None,
    ) -> dict:
        """
        Create a new service key via MPS API.

        For OSS mode: organization_id should be None
        For authenticated mode: organization_id should be provided
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_body = {
                "name": name,
                "description": description or f"Service key: {name}",
                "expires_in_days": expires_in_days,
                "created_by": created_by,
            }

            # Only add organization_id for non-OSS mode
            if DEPLOYMENT_MODE != "oss" and organization_id:
                request_body["organization_id"] = organization_id

            response = await client.post(
                f"{self.base_url}/api/v1/service-keys/",
                json=request_body,
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                data = response.json()
                # Transform the response to match our expected format
                return {
                    "id": data.get("id"),
                    "name": data.get("name") or name,
                    "service_key": data.get("service_key"),
                    "key_prefix": data.get("key_prefix")
                    or (
                        data.get("service_key", "")[:8]
                        if data.get("service_key")
                        else ""
                    ),
                    "expires_at": data.get("expires_at"),
                    "created_at": data.get("created_at"),
                    "is_active": data.get("is_active", True),
                    "created_by": data.get("created_by"),
                }
            else:
                raise httpx.HTTPStatusError(
                    f"Failed to create service key: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def get_service_keys(
        self,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[dict]:
        """
        Get service keys from MPS.

        For OSS mode: Use created_by to filter keys
        For authenticated mode: Use organization_id to filter keys
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            params = {}

            if DEPLOYMENT_MODE == "oss":
                # In OSS mode, filter by created_by
                if created_by:
                    params["created_by"] = created_by
            else:
                # In authenticated mode, filter by organization_id
                if organization_id:
                    params["organization_id"] = organization_id

            if include_archived:
                params["include_archived"] = "true"

            response = await client.get(
                f"{self.base_url}/api/v1/service-keys/",
                params=params,
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                keys = response.json()
                # Transform the response to match our expected format
                return [
                    {
                        "id": key.get("id"),
                        "name": key.get("name"),
                        "key_prefix": key.get("key_prefix", ""),
                        "is_active": key.get("is_active", True),
                        "created_at": key.get("created_at"),
                        "last_used_at": key.get("last_used_at"),
                        "expires_at": key.get("expires_at"),
                        "archived_at": key.get("archived_at"),
                        "created_by": key.get("created_by"),
                    }
                    for key in keys
                ]
            else:
                logger.error(
                    f"Failed to get service keys: {response.status_code} - {response.text}"
                )
                return []

    async def get_service_key_by_id(
        self,
        key_id: int,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Optional[dict]:
        """Get a specific service key by ID."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/service-keys/{key_id}",
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                key = response.json()

                # Validate ownership for OSS mode
                if DEPLOYMENT_MODE == "oss" and created_by:
                    if key.get("created_by") != created_by:
                        logger.warning(
                            f"Access denied: User {created_by} tried to access key created by {key.get('created_by')}"
                        )
                        return None

                # Validate organization for authenticated mode
                if DEPLOYMENT_MODE != "oss" and organization_id:
                    if key.get("organization_id") != organization_id:
                        logger.warning(
                            f"Access denied: Org {organization_id} tried to access key for org {key.get('organization_id')}"
                        )
                        return None

                return {
                    "id": key.get("id"),
                    "name": key.get("name"),
                    "key_prefix": key.get("key_prefix", ""),
                    "is_active": key.get("is_active", True),
                    "created_at": key.get("created_at"),
                    "last_used_at": key.get("last_used_at"),
                    "expires_at": key.get("expires_at"),
                    "archived_at": key.get("archived_at"),
                    "created_by": key.get("created_by"),
                }
            else:
                return None

    async def archive_service_key(
        self,
        key_id: int,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> bool:
        """
        Archive (soft delete) a service key.

        For OSS mode: Validates that created_by matches the key creator
        For authenticated mode: Validates organization_id matches
        """
        # First, verify ownership
        key = await self.get_service_key_by_id(key_id, organization_id, created_by)
        if not key:
            logger.error(f"Service key {key_id} not found or access denied")
            return False

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(
                f"{self.base_url}/api/v1/service-keys/{key_id}",
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code in [200, 204]:
                return True
            else:
                logger.error(
                    f"Failed to archive service key: {response.status_code} - {response.text}"
                )
                return False

    async def check_service_key_usage(
        self,
        service_key: str,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Check the usage and quota of a service key.

        Args:
            service_key: The service key to check usage for
            organization_id: Organization ID (for authenticated mode)
            created_by: User provider ID (for OSS mode)

        Returns:
            Dictionary containing:
            - total_credits_used: Total credits consumed
            - remaining_credits: Credits remaining in quota

        Raises:
            HTTPException: If the API call fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/service-keys/usage",
                json={"service_key": service_key},
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "total_credits_used": data.get("total_credits_used", 0.0),
                    "remaining_credits": data.get("remaining_credits", 0.0),
                }
            else:
                logger.error(
                    f"Failed to check service key usage: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to check service key usage: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def get_voices(
        self,
        provider: str,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Get available voices for a TTS provider from MPS.

        Args:
            provider: TTS provider name (elevenlabs, deepgram, sarvam, cartesia)
            organization_id: Organization ID (for authenticated mode)
            created_by: User provider ID (for OSS mode)

        Returns:
            Dictionary containing provider name and list of voices

        Raises:
            HTTPException: If the API call fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/voice-proxy/{provider}/voices",
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Failed to get voices for {provider}: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to get voices: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def call_workflow_api(
        self,
        call_type: str,
        use_case: str,
        activity_description: str,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Call the MPS workflow creation API using secret key authentication.

        For OSS mode: Pass created_by in headers
        For authenticated mode: Pass organization_id in headers

        Args:
            call_type: INBOUND or OUTBOUND
            use_case: Description of the use case
            activity_description: Description of what the agent should do
            organization_id: Organization ID (for authenticated mode)
            created_by: User provider ID (for OSS mode)

        Returns:
            Workflow data from MPS API

        Raises:
            HTTPException: If the API call fails
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/workflow/create-workflow",
                json={
                    "call_type": call_type,
                    "use_case": use_case,
                    "activity_description": activity_description,
                },
                headers=self._get_headers(organization_id, created_by),
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Failed to create workflow: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to create workflow: {response.text}",
                    request=response.request,
                    response=response,
                )


# Create a singleton instance
mps_service_key_client = MPSServiceKeyClient()
