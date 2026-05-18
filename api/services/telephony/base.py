"""
Base telephony provider interface for abstracting telephony services.
This allows easy switching between different providers (Twilio, Vonage, etc.)
while keeping business logic decoupled from specific implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from fastapi import WebSocket


@dataclass
class CallInitiationResult:
    """Standardized response from initiate_call across all providers."""

    call_id: str  # Provider's call identifier (SID for Twilio, UUID for Vonage)
    status: str  # Initial status (e.g., "queued", "initiated", "started")
    caller_number: Optional[str] = None  # Caller ID used for the outbound call
    provider_metadata: Dict[str, Any] = field(
        default_factory=dict
    )  # Data that needs to be persisted
    raw_response: Dict[str, Any] = field(
        default_factory=dict
    )  # Full provider response for debugging


@dataclass
class ProviderSyncResult:
    """Result of pushing a configuration change to the upstream provider.

    Used by ``configure_inbound`` (and similar provider-side syncs) so callers
    can surface a non-fatal warning to the user when the DB write succeeded
    but the provider API rejected the change.
    """

    ok: bool
    message: Optional[str] = None  # human-readable detail when ok=False


@dataclass
class NormalizedInboundData:
    """Standardized inbound call data across all providers."""

    provider: str  # Provider name (twilio, vobiz, etc.)
    call_id: str  # Provider's call identifier
    from_number: str  # Caller phone number (E.164 format)
    to_number: str  # Called phone number (E.164 format)
    direction: str  # Call direction (should be "inbound")
    call_status: str  # Call status (ringing, answered, etc.)
    account_id: Optional[str] = None  # Provider account ID
    from_country: Optional[str] = None  # Country code of caller
    to_country: Optional[str] = None  # Country code of called number
    raw_data: Dict[str, Any] = field(default_factory=dict)  # Original webhook data


class TelephonyProvider(ABC):
    """
    Abstract base class for telephony providers.
    All telephony providers must implement these core methods.
    """

    PROVIDER_NAME = None
    WEBHOOK_ENDPOINT = None

    @abstractmethod
    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call.

        Args:
            to_number: The destination phone number
            webhook_url: The URL to receive call events
            workflow_run_id: Optional workflow run ID for tracking
            from_number: Optional caller ID to use. If None, provider selects randomly.
            **kwargs: Provider-specific additional parameters

        Returns:
            CallInitiationResult with standardized call details
        """
        pass

    @abstractmethod
    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a call.

        Args:
            call_id: The provider-specific call identifier

        Returns:
            Dict containing call status information
        """
        pass

    @abstractmethod
    async def get_available_phone_numbers(self) -> List[str]:
        """
        Get list of available phone numbers for this provider.

        Returns:
            List of phone numbers that can be used for outbound calls
        """
        pass

    @abstractmethod
    def validate_config(self) -> bool:
        """
        Validate that the provider is properly configured.

        Returns:
            True if configuration is valid, False otherwise
        """
        pass

    @abstractmethod
    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """
        Verify webhook signature for security.

        Args:
            url: The webhook URL
            params: The webhook parameters
            signature: The signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        pass

    @abstractmethod
    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate the initial webhook response for starting a call session.

        Args:
            workflow_id: The workflow ID
            user_id: The user ID
            workflow_run_id: The workflow run ID

        Returns:
            Provider-specific response (e.g., TwiML for Twilio)
        """
        pass

    @abstractmethod
    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """
        Get cost information for a completed call.

        Args:
            call_id: Provider-specific call identifier (SID for Twilio, UUID for Vonage)

        Returns:
            Dict containing:
                - cost_usd: The cost in USD as float
                - duration: Call duration in seconds
                - status: Call completion status
                - raw_response: Full provider response for debugging
        """
        pass

    @abstractmethod
    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse provider-specific status callback data into generic format.

        Args:
            data: Raw callback data from the provider

        Returns:
            Dict with standardized fields:
                - call_id: Provider's call identifier
                - status: Standardized status (completed, failed, busy, etc.)
                - from_number: Optional caller number
                - to_number: Optional recipient number
                - duration: Optional call duration
                - extra: Provider-specific additional data
        """
        pass

    @abstractmethod
    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle provider-specific WebSocket connection for real-time call audio.

        This method encapsulates all provider-specific WebSocket handshake and
        message routing logic, keeping the main websocket endpoint clean.

        Args:
            websocket: The WebSocket connection
            workflow_id: The workflow ID
            user_id: The user ID
            workflow_run_id: The workflow run ID
        """
        pass

    # ======== INBOUND CALL METHODS ========

    @classmethod
    @abstractmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        Determine if this provider can handle the incoming webhook.

        Args:
            webhook_data: The parsed webhook payload
            headers: HTTP headers from the webhook request

        Returns:
            True if this provider should handle this webhook, False otherwise
        """
        pass

    @staticmethod
    @abstractmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Parse provider-specific inbound webhook data into normalized format.

        Args:
            webhook_data: Raw webhook data from the provider

        Returns:
            NormalizedInboundData with standardized fields
        """
        pass

    @staticmethod
    @abstractmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """
        Validate that the account_id from webhook matches the provider configuration.

        Args:
            config_data: Provider configuration data from organization
            webhook_account_id: Account ID from the webhook

        Returns:
            True if account_id matches, False otherwise
        """
        pass

    @abstractmethod
    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """
        Verify the signature of an inbound webhook for security.

        Each provider extracts its own signature/timestamp/nonce headers.
        Returning True when no signature is present means "no verification
        attempted" — providers should return False if a signature *is*
        present but invalid.

        Args:
            url: The full webhook URL the provider POSTed to
            webhook_data: Parsed webhook payload (form fields or JSON)
            headers: HTTP headers from the request (case-insensitive lookup
                is the provider's responsibility)
            body: Raw request body — only used by providers that sign over
                the body bytes (e.g. Vobiz)

        Returns:
            True if signature is valid (or none required), False otherwise
        """
        pass

    @abstractmethod
    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: "NormalizedInboundData",
        backend_endpoint: str,
    ) -> Any:
        """
        Bring up the inbound media stream for this provider and return the
        HTTP response body the webhook caller expects.

        Markup-response providers (Twilio, Plivo, Vobiz, ...) build and
        return their TwiML/XML/NCCO directly. Call-control providers
        (Telnyx) issue the REST calls needed to answer the call and start
        streaming, then return a simple acknowledgement.

        Args:
            websocket_url: WebSocket URL for audio streaming
            workflow_run_id: Workflow run ID for tracking
            normalized_data: Parsed inbound webhook payload (provides
                ``call_id`` for providers that need it)
            backend_endpoint: Public HTTPS base URL of this backend
                (already resolved by the caller); providers that need to
                build status / events URLs use this instead of re-fetching

        Returns:
            FastAPI Response object (or dict/JSON-serializable value)
        """
        pass

    async def handle_external_websocket(
        self,
        websocket: "WebSocket",
        *,
        organization_id: int,
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
        params: Dict[str, str],
    ) -> None:
        """Handle the agent-stream WebSocket where credentials are passed inline.

        Used by ``/api/v1/agent-stream/{workflow_uuid}`` when the caller carries
        provider credentials in the query string (no stored
        ``TelephonyConfigurationModel`` row required). ``organization_id`` is
        passed so providers can scope any config lookups to the workflow's
        org. Default raises so providers that haven't opted in fail loudly.
        """
        raise NotImplementedError(
            f"Agent-stream not supported for provider {self.PROVIDER_NAME}"
        )

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        """Sync inbound routing for ``address`` to the provider.

        ``webhook_url`` set: point the provider's resource for this number at
        the URL. ``None``: clear it. Default is a no-op for providers that
        don't support programmatic webhook configuration (e.g. ARI).
        """
        return ProviderSyncResult(ok=True)

    @staticmethod
    @abstractmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """
        Generate a provider-specific error response.

        Args:
            error_type: Type of error (auth_failed, not_configured, etc.)
            message: Error message

        Returns:
            Tuple of (Response, media_type) - Response object and content type
        """
        pass

    # ======== CALL TRANSFER METHODS ========

    @abstractmethod
    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Initiate a call transfer to a destination number.

        Args:
            destination: The destination phone number (E.164 format)
            transfer_id: Unique identifier for tracking this transfer
            conference_name: Name of the conference to join the destination into
            timeout: Transfer timeout in seconds
            **kwargs: Provider-specific additional parameters

        Returns:
            Dict containing:
                - call_sid: Provider's call identifier
                - status: Transfer initiation status
                - provider: Provider name

        Raises:
            NotImplementedError: If provider doesn't support transfers
            ValueError: If provider configuration is invalid
        """
        pass

    @abstractmethod
    def supports_transfers(self) -> bool:
        """
        Check if this provider supports call transfers.

        Returns:
            True if provider supports call transfers, False otherwise
        """
        pass
