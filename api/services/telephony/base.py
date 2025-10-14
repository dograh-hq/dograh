"""
Base telephony provider interface for abstracting telephony services.
This allows easy switching between different providers (Twilio, Vonage, etc.)
while keeping business logic decoupled from specific implementations.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class TelephonyProvider(ABC):
    """
    Abstract base class for telephony providers.
    All telephony providers must implement these core methods.
    """

    @abstractmethod
    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Initiate an outbound call.
        
        Args:
            to_number: The destination phone number
            webhook_url: The URL to receive call events
            workflow_run_id: Optional workflow run ID for tracking
            **kwargs: Provider-specific additional parameters
            
        Returns:
            Dict containing call details (provider-specific format)
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