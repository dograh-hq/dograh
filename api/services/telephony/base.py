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
    call_id: str                          # Provider's call identifier (SID for Twilio, UUID for Vonage)
    status: str                            # Initial status (e.g., "queued", "initiated", "started")
    provider_metadata: Dict[str, Any] = field(default_factory=dict)  # Data that needs to be persisted
    raw_response: Dict[str, Any] = field(default_factory=dict)       # Full provider response for debugging


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
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call.
        
        Args:
            to_number: The destination phone number
            webhook_url: The URL to receive call events
            workflow_run_id: Optional workflow run ID for tracking
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