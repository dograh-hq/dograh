"""Exotel telephony configuration schemas."""

from typing import List, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ALLOWED_API_BASE_URLS = frozenset(
    {
        "https://api.in.exotel.com",
        "https://api.exotel.com",
    }
)


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
    # Phone numbers are owned by telephony_phone_numbers / factory attach.
    from_numbers: List[str] = Field(default_factory=list)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        normalized = (value or "").rstrip("/")
        parsed = urlparse(normalized)
        origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if origin not in ALLOWED_API_BASE_URLS:
            raise ValueError(
                "api_base_url must be https://api.in.exotel.com or "
                "https://api.exotel.com"
            )
        return origin


class ExotelConfigurationResponse(BaseModel):
    provider: Literal["exotel"] = Field(default="exotel")
    account_sid: str  # Masked
    api_key: str  # Masked
    api_token: str  # Masked
    api_base_url: str
    from_numbers: List[str]
