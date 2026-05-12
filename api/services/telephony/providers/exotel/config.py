"""Exotel telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExotelConfigurationRequest(BaseModel):
    """Request schema for Exotel configuration."""

    provider: Literal["exotel"] = Field(default="exotel")
    api_key: str = Field(..., description="Exotel API Key (from Dashboard → Settings → API Settings)")
    api_token: str = Field(..., description="Exotel API Token")
    account_sid: str = Field(..., description="Exotel Account SID (subdomain/account identifier)")
    subdomain: str = Field(
        default="api.exotel.com",
        description=(
            "Exotel API subdomain. Use 'api.exotel.com' for global (SEA), "
            "'api.in.exotel.com' for India-hosted accounts."
        ),
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of Exotel ExoPhone numbers (CallerIds) used for outbound calls",
    )
    app_id: Optional[str] = Field(
        default=None,
        description=(
            "Exotel App ID (from App Bazaar → My Apps). "
            "When set, used as the Url for inbound call flows. "
            "Leave blank if managing inbound via the Dograh answer URL."
        ),
    )


class ExotelConfigurationResponse(BaseModel):
    """Response schema for Exotel configuration with masked sensitive fields."""

    provider: Literal["exotel"] = Field(default="exotel")
    api_key: str  # Masked
    api_token: str  # Masked
    account_sid: str
    subdomain: str
    from_numbers: List[str]
    app_id: Optional[str] = None
