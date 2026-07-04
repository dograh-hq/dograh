"""WhatsApp Platform telephony configuration schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class WhatsAppPlatformConfigurationRequest(BaseModel):
    """Request schema for WhatsApp Platform configuration.

    Stored encrypted in Dograh's telephony_configurations table.
    """

    provider: Literal["whatsapp_platform"] = Field(default="whatsapp_platform")

    platform_api_url: str = Field(
        ...,
        description=(
            "Base URL of the WhatsApp SaaS backend. "
            "Example: https://api.yourdomain.com"
        ),
    )
    webhook_secret: str = Field(
        ...,
        description=(
            "Shared secret sent as X-Dograh-Key header on every webhook call "
            "from Dograh → Platform. Must be >= 32 characters."
        ),
        min_length=8,
    )


class WhatsAppPlatformConfigurationResponse(BaseModel):
    """Response schema — webhook_secret is masked for security."""

    id: int
    provider: Literal["whatsapp_platform"] = "whatsapp_platform"
    platform_api_url: str
    webhook_secret: str = "••••••••••••••••"  # always masked
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
