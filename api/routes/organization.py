from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from typing import Optional, Union
from api.schemas.telephony_config import (
    TelephonyConfigurationResponse,
    TwilioConfigurationRequest,
    TwilioConfigurationResponse,
    VonageConfigurationRequest,
    VonageConfigurationResponse,
)
from api.services.auth.depends import get_user
from api.services.configuration.masking import is_mask_of, mask_key

router = APIRouter(prefix="/organizations", tags=["organizations"])


# TODO: Make endpoints provider-agnostic
@router.get("/telephony-config", response_model=TelephonyConfigurationResponse)
async def get_telephony_configuration(
    user: UserModel = Depends(get_user),
    provider: Optional[str] = None  # Query param to filter by provider
):
    """Get telephony configuration for the user's organization with masked sensitive fields.
    
    Args:
        provider: Optional provider filter ('twilio' or 'vonage'). 
                 If specified, only returns config if it matches the stored provider.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Try new key first, fallback to old for backward compatibility
    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )
    
    # TODO: Remove after telephony provider db migration is complete
    if not config:
        config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
        )

    if not config or not config.value:
        return TelephonyConfigurationResponse(twilio=None, vonage=None)

    # Simple single-provider format
    stored_provider = config.value.get("provider", "twilio")
    
    # If a specific provider is requested, only return config if it matches
    if provider and provider != stored_provider:
        # User is requesting a different provider than what's stored
        return TelephonyConfigurationResponse(twilio=None, vonage=None)
    
    if stored_provider == "twilio":
        # Mask sensitive fields (account_sid and auth_token) before returning
        account_sid = config.value.get("account_sid", "")
        auth_token = config.value.get("auth_token", "")

        return TelephonyConfigurationResponse(
            twilio=TwilioConfigurationResponse(
                provider="twilio",
                account_sid=mask_key(account_sid) if account_sid else "",
                auth_token=mask_key(auth_token) if auth_token else "",
                from_numbers=config.value.get("from_numbers", []),
            ),
            vonage=None
        )
    elif stored_provider == "vonage":
        # Mask sensitive fields for Vonage
        application_id = config.value.get("application_id", "")
        private_key = config.value.get("private_key", "")
        api_key = config.value.get("api_key", "")
        api_secret = config.value.get("api_secret", "")
        
        return TelephonyConfigurationResponse(
            twilio=None,
            vonage=VonageConfigurationResponse(
                provider="vonage",
                application_id=application_id,  # Not masked, not sensitive
                private_key=mask_key(private_key) if private_key else "",
                api_key=mask_key(api_key) if api_key else None,
                api_secret=mask_key(api_secret) if api_secret else None,
                from_numbers=config.value.get("from_numbers", []),
            )
        )
    else:
        return TelephonyConfigurationResponse(twilio=None, vonage=None)


@router.post("/telephony-config")
async def save_telephony_configuration(
    request: Union[TwilioConfigurationRequest, VonageConfigurationRequest], 
    user: UserModel = Depends(get_user)
):
    """Save telephony configuration for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Fetch existing configuration to handle masked values
    existing_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )
    if not existing_config:
        # Check old key for backward compatibility
        existing_config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
        )

    # Build simple single-provider configuration
    if request.provider == "twilio":
        config_value = {
            "provider": "twilio",
            "account_sid": request.account_sid,
            "auth_token": request.auth_token,
            "from_numbers": request.from_numbers,
        }
    elif request.provider == "vonage":
        config_value = {
            "provider": "vonage",
            "application_id": request.application_id,
            "private_key": request.private_key,
            "api_key": getattr(request, 'api_key', None),
            "api_secret": getattr(request, 'api_secret', None),
            "from_numbers": request.from_numbers,
        }
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {request.provider}")

    # Handle masked values - only if same provider
    if existing_config and existing_config.value:
        existing_provider = existing_config.value.get("provider")
        
        # Only preserve masked values if it's the same provider
        if existing_provider == request.provider:
            if request.provider == "twilio":
                # Check if account_sid is unchanged (masked value matches)
                if hasattr(request, 'account_sid') and is_mask_of(request.account_sid, existing_config.value.get("account_sid", "")):
                    config_value["account_sid"] = existing_config.value["account_sid"]  # Keep original
                
                # Check if auth_token is unchanged (masked value matches)
                if hasattr(request, 'auth_token') and is_mask_of(request.auth_token, existing_config.value.get("auth_token", "")):
                    config_value["auth_token"] = existing_config.value["auth_token"]  # Keep original
                    
            elif request.provider == "vonage":
                # Check if private_key is unchanged (masked value matches)
                if hasattr(request, 'private_key') and is_mask_of(request.private_key, existing_config.value.get("private_key", "")):
                    config_value["private_key"] = existing_config.value["private_key"]  # Keep original
                
                # Check if api_key is unchanged (masked value matches)
                if hasattr(request, 'api_key') and request.api_key and is_mask_of(request.api_key, existing_config.value.get("api_key", "")):
                    config_value["api_key"] = existing_config.value["api_key"]  # Keep original
                
                # Check if api_secret is unchanged (masked value matches)
                if hasattr(request, 'api_secret') and request.api_secret and is_mask_of(request.api_secret, existing_config.value.get("api_secret", "")):
                    config_value["api_secret"] = existing_config.value["api_secret"]  # Keep original

    # Always save to new TELEPHONY_CONFIGURATION key
    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
        config_value,
    )
    
    # If old TWILIO_CONFIGURATION exists, delete it to avoid confusion
    if existing_config and existing_config.key == OrganizationConfigurationKey.TWILIO_CONFIGURATION.value:
        # Note: We're migrating from old to new key
        logger.info(f"Migrated telephony config from TWILIO_CONFIGURATION to TELEPHONY_CONFIGURATION for org {user.selected_organization_id}")

    return {"message": "Telephony configuration saved successfully"}
