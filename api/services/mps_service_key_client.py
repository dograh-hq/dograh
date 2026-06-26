"""
MPS Service Key HTTP Client
This client communicates with the Model Proxy Service (MPS) for service key management.
Service keys are stored and managed entirely in MPS, not in the local database.
"""

import asyncio
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
            response = await client.get(
                f"{self.base_url}/api/v1/service-keys/usage/self",
                headers={
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "total_credits_used": data.get("total_credits_used", 0.0),
                    "remaining_credits": data.get("remaining_credits", 0.0),
                }
            else:
                logger.warning(
                    f"Failed to check service key usage: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to check service key usage: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def get_usage_by_created_by(self, created_by: str) -> dict:
        """
        Get aggregated usage for all service keys created by a user (OSS mode).

        Args:
            created_by: The user's provider ID

        Returns:
            Dictionary containing total_credits_used and remaining_credits
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/service-keys/usage/created-by",
                json={"created_by": created_by},
                headers=self._get_headers(created_by=created_by),
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "total_credits_used": data.get("total_credits_used", 0.0),
                    "remaining_credits": data.get("remaining_credits", 0.0),
                }
            else:
                logger.error(
                    f"Failed to get usage by created_by: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to get usage by created_by: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def get_usage_by_organization(self, organization_id: int) -> dict:
        """
        Get aggregated usage for all service keys belonging to an organization (hosted mode).

        Args:
            organization_id: The organization's ID

        Returns:
            Dictionary containing total_credits_used and remaining_credits
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/service-keys/usage/organization",
                json={"organization_id": organization_id},
                headers=self._get_headers(organization_id=organization_id),
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "total_credits_used": data.get("total_credits_used", 0.0),
                    "remaining_credits": data.get("remaining_credits", 0.0),
                }
            else:
                logger.error(
                    f"Failed to get usage by organization: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to get usage by organization: {response.text}",
                    request=response.request,
                    response=response,
                )

    async def create_credit_purchase_url(
        self,
        organization_id: int,
        created_by: Optional[str] = None,
        return_url: Optional[str] = None,
        billing_details: Optional[dict] = None,
    ) -> dict:
        """Create a short-lived MPS checkout URL for adding organization credits."""
        payload = {
            "created_by": created_by,
            "return_url": return_url,
            "billing_details": billing_details or {},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/billing/accounts/{organization_id}/checkout-sessions",
                json=payload,
                headers=self._get_headers(
                    organization_id=organization_id,
                    created_by=created_by,
                ),
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to create MPS credit purchase URL: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to create MPS credit purchase URL: {response.text}",
                request=response.request,
                response=response,
            )

    async def get_credit_ledger(
        self,
        organization_id: int,
        page: int = 1,
        limit: int = 50,
        created_by: Optional[str] = None,
    ) -> dict:
        """Get the MPS v2 billing account balance and recent credit ledger."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/billing/accounts/{organization_id}/ledger",
                params={"page": page, "limit": limit},
                headers=self._get_headers(
                    organization_id=organization_id,
                    created_by=created_by,
                ),
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to get MPS credit ledger: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to get MPS credit ledger: {response.text}",
                request=response.request,
                response=response,
            )

    async def get_billing_account_status(
        self,
        organization_id: int,
        created_by: Optional[str] = None,
    ) -> Optional[dict]:
        """Get an existing MPS v2 billing account without creating one."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/billing/accounts/{organization_id}/status",
                headers=self._get_headers(
                    organization_id=organization_id,
                    created_by=created_by,
                ),
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to get MPS billing account status: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to get MPS billing account status: {response.text}",
                request=response.request,
                response=response,
            )

    async def ensure_billing_account_v2(
        self,
        organization_id: int,
        created_by: Optional[str] = None,
    ) -> dict:
        """Create or return the MPS v2 billing account for an organization."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1/billing/accounts/{organization_id}/balance",
                headers=self._get_headers(
                    organization_id=organization_id,
                    created_by=created_by,
                ),
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to ensure MPS billing account v2: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to ensure MPS billing account v2: {response.text}",
                request=response.request,
                response=response,
            )

    async def authorize_workflow_run_start(
        self,
        *,
        organization_id: int,
        workflow_run_id: int | None = None,
        service_key: Optional[str] = None,
        require_correlation_id: bool = False,
        minimum_credits: float | None = None,
        metadata: Optional[dict] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """Authorize a hosted workflow run and optionally mint its MPS correlation."""
        payload = {
            "workflow_run_id": workflow_run_id,
            "service_key": service_key,
            "require_correlation_id": require_correlation_id,
            "metadata": metadata or {},
        }
        if minimum_credits is not None:
            payload["minimum_credits"] = minimum_credits

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/billing/accounts/{organization_id}/run-authorization",
                json=payload,
                headers=self._get_headers(
                    organization_id=organization_id,
                    created_by=created_by,
                ),
            )

            if response.status_code == 200:
                return response.json()

            logger.warning(
                "Failed to authorize MPS workflow run start: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to authorize MPS workflow run start: {response.text}",
                request=response.request,
                response=response,
            )

    async def create_correlation_id(
        self,
        *,
        service_key: str,
        workflow_run_id: int | None = None,
    ) -> dict:
        """Mint a server-generated correlation ID for managed model services."""
        payload: dict[str, int] = {}
        if workflow_run_id is not None:
            payload["workflow_run_id"] = workflow_run_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/service-keys/correlation-id/self",
                json=payload,
                headers={
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to create correlation ID: "
                f"{response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to create correlation ID: {response.text}",
                request=response.request,
                response=response,
            )

    async def report_platform_usage(
        self,
        *,
        organization_id: int,
        correlation_id: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        workflow_run_id: int | None = None,
        metadata: Optional[dict] = None,
        max_attempts: int = 3,
    ) -> dict:
        """Report hosted Dograh platform usage for a completed workflow run."""
        if DEPLOYMENT_MODE == "oss":
            raise ValueError("OSS deployments must not report platform usage to MPS")
        if not correlation_id and duration_seconds is None:
            raise ValueError(
                "Platform usage reports require correlation_id or duration_seconds"
            )

        payload: dict = {
            "metadata": metadata or {},
        }
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        if workflow_run_id is not None:
            payload["workflow_run_id"] = workflow_run_id

        max_attempts = max(1, max_attempts)
        last_response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, max_attempts + 1):
                response = await client.post(
                    (
                        f"{self.base_url}/api/v1/billing/accounts/"
                        f"{organization_id}/platform-usage"
                    ),
                    json=payload,
                    headers=self._get_headers(organization_id=organization_id),
                )
                last_response = response

                if response.status_code == 200:
                    return response.json()

                usage_not_ready = (
                    response.status_code == 409 and "usage_not_ready" in response.text
                )
                if usage_not_ready and attempt < max_attempts:
                    await asyncio.sleep(attempt)
                    continue

                log = logger.warning if usage_not_ready else logger.error
                log(
                    "Failed to report platform usage: "
                    f"{response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to report platform usage: {response.text}",
                    request=response.request,
                    response=response,
                )

        raise httpx.HTTPStatusError(
            "Failed to report platform usage",
            request=last_response.request,
            response=last_response,
        )

    async def transcribe_audio(
        self,
        audio_data: bytes,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: str = "en",
        model: str = "default",
        correlation_id: Optional[str] = None,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Transcribe an audio file via MPS STT API.

        Args:
            audio_data: Raw audio bytes
            filename: Name of the audio file
            content_type: MIME type of the audio (e.g., audio/wav, audio/mp3)
            language: Language code for transcription (default: "en")
            model: Model tier name (default: "default")
            correlation_id: Optional correlation ID for tracking
            organization_id: Organization ID (for authenticated mode)
            created_by: User provider ID (for OSS mode)

        Returns:
            Dictionary containing transcription result with keys like
            'transcript', 'duration_seconds', etc.

        Raises:
            httpx.HTTPStatusError: If the API call fails
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            files = {
                "file": (filename, audio_data, content_type),
            }
            data = {
                "language": language,
                "model": model,
            }
            if correlation_id:
                data["correlation_id"] = correlation_id

            headers = self._get_headers(organization_id, created_by)
            # Remove Content-Type so httpx sets the correct multipart boundary
            headers.pop("Content-Type", None)

            response = await client.post(
                f"{self.base_url}/api/v1/stt/transcribe",
                files=files,
                data=data,
                headers=headers,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Failed to transcribe audio: {response.status_code} - {response.text}"
                )
                raise httpx.HTTPStatusError(
                    f"Failed to transcribe audio: {response.text}",
                    request=response.request,
                    response=response,
                )

    def validate_service_key(
        self,
        service_key: str,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> bool:
        """
        Synchronously validate a Dograh service key by checking usage via MPS.

        Returns True if the key is valid, False otherwise.
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/v1/service-keys/usage/self",
                    headers={
                        "Authorization": f"Bearer {service_key}",
                        "Content-Type": "application/json",
                    },
                )
                return response.status_code == 200
        except Exception:
            logger.warning("Failed to validate Dograh service key via MPS")
            return False

    async def get_voices(
        self,
        provider: str,
        model: Optional[str] = None,
        language: Optional[str] = None,
        q: Optional[str] = None,
        gender: Optional[str] = None,
        accent: Optional[str] = None,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """
        Get available voices for a TTS provider from MPS.

        Args:
            provider: TTS provider name (elevenlabs, deepgram, sarvam, cartesia, rime)
            model: Optional model ID to filter voices (e.g., "arcana", "mistv2")
            language: Optional language code to filter voices (e.g., "eng", "en")
            organization_id: Organization ID (for authenticated mode)
            created_by: User provider ID (for OSS mode)

        Returns:
            Dictionary containing provider name and list of voices

        Raises:
            HTTPException: If the API call fails
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            params = {}
            if model:
                params["model"] = model
            if language:
                params["language"] = language
            if q:
                params["q"] = q
            if gender:
                params["gender"] = gender
            if accent:
                params["accent"] = accent
            response = await client.get(
                f"{self.base_url}/api/v1/voice-proxy/{provider}/voices",
                headers=self._get_headers(organization_id, created_by),
                params=params,
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

    async def process_document(
        self,
        file_path: str,
        filename: str,
        content_type: str,
        retrieval_mode: str = "chunked",
        max_tokens: int = 128,
        chunk_overlap_tokens: int = 0,
        merge_peers: bool = True,
        tokenizer_model: Optional[str] = None,
        correlation_id: Optional[str] = None,
        organization_id: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> dict:
        """Convert + chunk a document via MPS /document/process.

        Returns a dict matching DocumentProcessResponse in MPS:
            {
              "mode": "chunked" | "full_document",
              "docling_metadata": {...},
              "full_text": str | None,   # populated only in full_document mode
              "chunks": [...],           # populated only in chunked mode
            }

        Timeout is 300s to match the ALB idle_timeout configured in
        infrastructure/mps/main.tf. Raises on non-2xx responses.
        """
        data = {
            "retrieval_mode": retrieval_mode,
            "max_tokens": str(max_tokens),
            "chunk_overlap_tokens": str(chunk_overlap_tokens),
            "merge_peers": str(merge_peers).lower(),
        }
        if tokenizer_model is not None:
            data["tokenizer_model"] = tokenizer_model
        if correlation_id:
            data["correlation_id"] = correlation_id

        headers = self._get_headers(organization_id, created_by)
        # Remove JSON content-type so httpx sets the correct multipart boundary.
        headers.pop("Content-Type", None)

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            with open(file_path, "rb") as fh:
                files = {"file": (filename, fh.read(), content_type)}

            response = await client.post(
                f"{self.base_url}/api/v1/document/process",
                files=files,
                data=data,
                headers=headers,
            )

            if response.status_code == 200:
                return response.json()

            logger.error(
                f"Failed to process document: {response.status_code} - {response.text}"
            )
            raise httpx.HTTPStatusError(
                f"Failed to process document: {response.text}",
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
