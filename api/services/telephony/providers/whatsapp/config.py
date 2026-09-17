"""WhatsApp telephony configuration schemas.

This module defines Pydantic models for WhatsApp Business API configuration,
following Dograh's telephony provider configuration pattern.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class WhatsAppConfigurationRequest(BaseModel):
    """Request schema for WhatsApp configuration.
    
    This schema validates incoming configuration save requests and
    integrates with Dograh's metadata-driven UI forms.
    
    Attributes:
        provider: Literal discriminator for union typing
        access_token: WhatsApp Business API access token
        phone_number_id: Business phone number ID from Meta
        webhook_verify_token: Token for webhook verification
        app_secret: App secret for webhook signature validation
        business_initiated_calls_enabled: Enable outbound calling
        call_icon_visibility: Control call icon display in WhatsApp
    """
    
    provider: Literal["whatsapp"] = Field(default="whatsapp")
    access_token: str = Field(..., min_length=1, description="WhatsApp Business API access token")
    phone_number_id: str = Field(..., min_length=1, description="Business phone number ID")
    webhook_verify_token: str = Field(..., min_length=1, description="Webhook verification token")
    app_secret: str = Field(..., min_length=1, description="App secret for webhook signature validation")
    business_initiated_calls_enabled: bool = Field(
        default=False,
        description="Enable business-initiated calls to WhatsApp users"
    )
    call_icon_visibility: Literal["enabled", "disabled", "business_hours"] = Field(
        default="enabled",
        description="Control when call icon appears to users"
    )


class WhatsAppConfigurationResponse(BaseModel):
    """Response schema for WhatsApp configuration.

    This schema defines the masked response returned to the UI,
    ensuring sensitive credentials are never exposed unmasked.
    """

    provider: Literal["whatsapp"] = Field(default="whatsapp")
    phone_number_id: str
    access_token: Optional[str] = None
    app_secret: Optional[str] = None
    webhook_verify_token: Optional[str] = None
    business_initiated_calls_enabled: bool = False
    call_icon_visibility: Literal["enabled", "disabled", "business_hours"] = "enabled"

