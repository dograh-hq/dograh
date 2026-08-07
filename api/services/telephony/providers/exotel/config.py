"""Exotel telephony configuration schemas."""

from typing import List, Literal

from pydantic import BaseModel, Field


class ExotelConfigurationRequest(BaseModel):
    provider: Literal["exotel"] = Field(default="exotel")
    account_sid: str = Field(..., description="Exotel Account SID")
    api_key: str = Field(..., description="Exotel API Key")
    api_token: str = Field(..., description="Exotel API Token")
    api_base_url: str = Field(
        default="https://api.in.exotel.com",
        description=(
            "Exotel API base URL. Use https://api.in.exotel.com for India "
            "or https://api.exotel.com for other regions."
        ),
    )
    from_numbers: List[str] = Field(default_factory=list)


class ExotelConfigurationResponse(BaseModel):
    provider: Literal["exotel"] = Field(default="exotel")
    account_sid: str
    api_key: str  # Masked
    api_token: str  # Masked
    api_base_url: str
    from_numbers: List[str]
