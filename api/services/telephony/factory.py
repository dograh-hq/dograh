"""
Factory for creating telephony providers.
Handles configuration loading from environment (OSS) or database (SaaS).
The providers themselves don't know or care where config comes from.
"""
import os
from typing import Any, Dict, Optional

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.telephony.base import TelephonyProvider
from api.services.telephony.providers.twilio_provider import TwilioProvider


async def load_telephony_config(organization_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Load telephony configuration from appropriate source.
    
    Args:
        organization_id: Organization ID for database config (SaaS mode)
                        None for environment config (OSS mode)
    
    Returns:
        Configuration dictionary with provider type and credentials
    """
    if organization_id:
        # SaaS mode: Load from database
        logger.debug(f"Loading telephony config from database for org {organization_id}")
        
        # TODO: Use TELEPHONY_CONFIGURATION
        twilio_config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
        )
        
        if twilio_config and twilio_config.value:
            # TODO: Get provider from config
            return {
                "provider": "twilio",
                "account_sid": twilio_config.value.get("account_sid"),
                "auth_token": twilio_config.value.get("auth_token"),
                "from_numbers": twilio_config.value.get("from_numbers", [])
            }
        
        raise ValueError(f"No telephony configuration found for organization {organization_id}")
    
    else:
        # OSS mode: Load from environment variables
        logger.debug("Loading telephony config from environment variables")
        
        provider = os.getenv("TELEPHONY_PROVIDER", "twilio").lower()
        
        if provider == "twilio":
            # Load Twilio config from env
            account_sid = os.getenv("TWILIO_ACCOUNT_SID")
            auth_token = os.getenv("TWILIO_AUTH_TOKEN")
            from_number = os.getenv("TWILIO_FROM_NUMBER")
            
            if not all([account_sid, auth_token, from_number]):
                raise ValueError(
                    "Missing Twilio configuration. Please set TWILIO_ACCOUNT_SID, "
                    "TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER environment variables."
                )
            
            return {
                "provider": "twilio",
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_numbers": [from_number] if isinstance(from_number, str) else from_number
            }
        
        # Future providers can be added here
        # elif provider == "vonage":
        #     return {
        #         "provider": "vonage",
        #         "api_key": os.getenv("VONAGE_API_KEY"),
        #         "api_secret": os.getenv("VONAGE_API_SECRET"),
        #         "from_numbers": [os.getenv("VONAGE_FROM_NUMBER")]
        #     }
        
        else:
            raise ValueError(f"Unknown telephony provider: {provider}")


async def get_telephony_provider(
    organization_id: Optional[int] = None
) -> TelephonyProvider:
    """
    Factory function to create telephony providers.
    
    Args:
        organization_id: Organization ID for SaaS mode (optional)
        
    Returns:
        Configured telephony provider instance
        
    Raises:
        ValueError: If provider type is unknown or configuration is invalid
    """
    # Load configuration from appropriate source
    config = await load_telephony_config(organization_id)
    
    provider_type = config.get("provider", "twilio")
    logger.info(f"Creating {provider_type} telephony provider")
    
    # Create provider instance with configuration
    # Provider doesn't know or care if config came from env or database
    if provider_type == "twilio":
        return TwilioProvider(config)
    
    # Future providers can be added here
    # elif provider_type == "vonage":
    #     return VonageProvider(config)
    # elif provider_type == "plivo":
    #     return PlivoProvider(config)
    
    else:
        raise ValueError(f"Unknown telephony provider: {provider_type}")