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
from api.services.telephony.providers.vonage_provider import VonageProvider


async def load_telephony_config(organization_id: int) -> Dict[str, Any]:
    """
    Load telephony configuration from database.
    
    Args:
        organization_id: Organization ID for database config
    
    Returns:
        Configuration dictionary with provider type and credentials
    
    Raises:
        ValueError: If no configuration found for the organization
    """
    if not organization_id:
        raise ValueError("Organization ID is required to load telephony configuration")
    
    logger.debug(f"Loading telephony config from database for org {organization_id}")
    
    config = await db_client.get_configuration(
        organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )
    
    if config and config.value:
        # Simple single-provider format
        provider = config.value.get("provider", "twilio")
        
        if provider == "twilio":
            return {
                "provider": "twilio",
                "account_sid": config.value.get("account_sid"),
                "auth_token": config.value.get("auth_token"),
                "from_numbers": config.value.get("from_numbers", [])
            }
        elif provider == "vonage":
            return {
                "provider": "vonage",
                "application_id": config.value.get("application_id"),
                "private_key": config.value.get("private_key"),
                "api_key": config.value.get("api_key"),
                "api_secret": config.value.get("api_secret"),
                "from_numbers": config.value.get("from_numbers", [])
            }
        else:
            raise ValueError(f"Unknown provider in config: {provider}")
    
    raise ValueError(f"No telephony configuration found for organization {organization_id}")


async def get_telephony_provider(
    organization_id: int
) -> TelephonyProvider:
    """
    Factory function to create telephony providers.
    
    Args:
        organization_id: Organization ID (required)
        
    Returns:
        Configured telephony provider instance
        
    Raises:
        ValueError: If provider type is unknown or configuration is invalid
    """
    # Load configuration
    config = await load_telephony_config(organization_id)
    
    provider_type = config.get("provider", "twilio")
    logger.info(f"Creating {provider_type} telephony provider")
    
    # Create provider instance with configuration
    if provider_type == "twilio":
        return TwilioProvider(config)
    
    elif provider_type == "vonage":
        return VonageProvider(config)
    
    else:
        raise ValueError(f"Unknown telephony provider: {provider_type}")
