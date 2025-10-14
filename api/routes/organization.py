from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from api.schemas.telephony_config import (
    TelephonyConfigurationResponse,
    TwilioConfigurationRequest,
    TwilioConfigurationResponse,
)
from api.services.auth.depends import get_user
from api.services.configuration.masking import is_mask_of, mask_key

router = APIRouter(prefix="/organizations", tags=["organizations"])


# TODO: Make endpoints provider-agnostic
@router.get("/telephony-config", response_model=TelephonyConfigurationResponse)
async def get_telephony_configuration(user: UserModel = Depends(get_user)):
    """Get telephony configuration for the user's organization with masked sensitive fields."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,  # TODO: Use TELEPHONY_CONFIGURATION
    )

    if not config or not config.value:
        return TelephonyConfigurationResponse(twilio=None)

    # Mask sensitive fields (account_sid and auth_token) before returning
    account_sid = config.value.get("account_sid", "")
    auth_token = config.value.get("auth_token", "")

    return TelephonyConfigurationResponse(
        twilio=TwilioConfigurationResponse(
            provider="twilio",
            account_sid=mask_key(account_sid) if account_sid else "",
            auth_token=mask_key(auth_token) if auth_token else "",
            from_numbers=config.value.get("from_numbers", []),
        )
    )


@router.post("/telephony-config")
async def save_telephony_configuration(
    request: TwilioConfigurationRequest, user: UserModel = Depends(get_user)
):
    """Save telephony configuration for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Fetch existing configuration to handle masked values
    existing_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,  # TODO: Use TELEPHONY_CONFIGURATION
    )

    # Build new configuration
    config_value = {
        "provider": request.provider,
        "account_sid": request.account_sid,
        "auth_token": request.auth_token,
        "from_numbers": request.from_numbers,
    }

    # If incoming values are masked (same as stored masked value), keep the original
    if existing_config and existing_config.value:
        # Check if account_sid is unchanged (masked value matches)
        stored_account_sid = existing_config.value.get("account_sid", "")
        if stored_account_sid and is_mask_of(request.account_sid, stored_account_sid):
            config_value["account_sid"] = stored_account_sid  # Keep original

        # Check if auth_token is unchanged (masked value matches)
        stored_auth_token = existing_config.value.get("auth_token", "")
        if stored_auth_token and is_mask_of(request.auth_token, stored_auth_token):
            config_value["auth_token"] = stored_auth_token  # Keep original

    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
        config_value,
    )

    return {"message": "Telephony configuration saved successfully"}
