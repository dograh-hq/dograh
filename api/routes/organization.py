from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from typing import Union
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

# Provider configuration constants
PROVIDER_MASKED_FIELDS = {
    "twilio": ["account_sid", "auth_token"],
    "vonage": ["private_key", "api_key", "api_secret"]
}

# TODO: Make endpoints provider-agnostic
@router.get("/telephony-config", response_model=TelephonyConfigurationResponse)
async def get_telephony_configuration(
    user: UserModel = Depends(get_user)
):
    """Get telephony configuration for the user's organization with masked sensitive fields."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )

    if not config or not config.value:
        return TelephonyConfigurationResponse()

    stored_provider = config.value.get("provider", "twilio")
    
    if stored_provider == "twilio":
        account_sid = config.value.get("account_sid", "")
        auth_token = config.value.get("auth_token", "")
        from_numbers = config.value.get("from_numbers", []) if account_sid and auth_token else []

        return TelephonyConfigurationResponse(
            twilio=TwilioConfigurationResponse(
                provider="twilio",
                account_sid=mask_key(account_sid) if account_sid else "",
                auth_token=mask_key(auth_token) if auth_token else "",
                from_numbers=from_numbers,
            ),
            vonage=None
        )
    elif stored_provider == "vonage":
        application_id = config.value.get("application_id", "")
        private_key = config.value.get("private_key", "")
        api_key = config.value.get("api_key", "")
        api_secret = config.value.get("api_secret", "")
        from_numbers = config.value.get("from_numbers", []) if application_id and private_key else []
        
        return TelephonyConfigurationResponse(
            twilio=None,
            vonage=VonageConfigurationResponse(
                provider="vonage",
                application_id=application_id,
                private_key=mask_key(private_key) if private_key else "",
                api_key=mask_key(api_key) if api_key else None,
                api_secret=mask_key(api_secret) if api_secret else None,
                from_numbers=from_numbers,
            )
        )
    else:
        return TelephonyConfigurationResponse()


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

    # Build single-provider configuration
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

    if existing_config and existing_config.value:
        existing_provider = existing_config.value.get("provider")

        if existing_provider == request.provider:
            preserve_masked_fields(request, existing_config, config_value)

    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
        config_value,
    )

    return {"message": "Telephony configuration saved successfully"}

def preserve_masked_fields(request, existing_config, config_value):

    provider = request.provider
    masked_fields = PROVIDER_MASKED_FIELDS.get(provider, [])
    
    for field_name in masked_fields:
        if hasattr(request, field_name):
            field_value = getattr(request, field_name)
            # Check if field has a value and is a masked version of the existing value
            if field_value and is_mask_of(field_value, existing_config.value.get(field_name, "")):
                config_value[field_name] = existing_config.value[field_name]
