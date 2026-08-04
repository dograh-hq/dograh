"""VoxPro telephony configuration schemas.

VoxPro is a licensed Indian VNO. Unlike self-hosted ARI, the Dograh user does
NOT expose their own Asterisk — they authenticate to VoxPro's managed connector
API with a per-tenant API key, and VoxPro originates/bridges the call on its own
carrier and streams Plivo/Twilio-standard mu-law audio to Dograh.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

DEFAULT_API_BASE = "https://connector.voxprosolutions.com"


class VoxProConfigurationRequest(BaseModel):
    """Request schema for VoxPro configuration."""

    provider: Literal["voxpro"] = Field(default="voxpro")
    api_key: str = Field(..., description="VoxPro connector API key (per-tenant)")
    tenant_id: str = Field(
        ..., description="VoxPro tenant / X-Tenant-ID (e.g., AI_Katha_1783948668)"
    )
    api_base: str = Field(
        default=DEFAULT_API_BASE,
        description="Base URL of the VoxPro AI Connector",
    )
    from_numbers: List[str] = Field(
        default_factory=list,
        description="VoxPro DIDs available for outbound caller-ID (E.164, no + prefix)",
    )


class VoxProConfigurationResponse(BaseModel):
    """Response schema for VoxPro configuration with masked sensitive fields."""

    provider: Literal["voxpro"] = Field(default="voxpro")
    api_key: str  # Masked
    tenant_id: str
    api_base: str = DEFAULT_API_BASE
    from_numbers: List[str]
