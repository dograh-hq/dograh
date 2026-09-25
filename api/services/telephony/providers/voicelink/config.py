"""VoiceLink telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

PRODUCTION_API_BASE_URL = "https://app.voicelink.co.in/api"
UAT_API_BASE_URL = "https://voicelinkuat.elisiontec.com/api"

ALLOWED_API_BASE_URLS = frozenset({PRODUCTION_API_BASE_URL, UAT_API_BASE_URL})


class VoiceLinkConfigurationRequest(BaseModel):
    provider: Literal["voicelink"] = Field(default="voicelink")
    api_token: str = Field(..., description="VoiceLink API token")
    # Stored as text: Dograh matches inbound streams to a configuration with
    # ``credentials->>'client_id'``, and Postgres will not compare text to an
    # integer. The form sends a number, so it is coerced here.
    client_id: Optional[str] = Field(
        default=None,
        description="VoiceLink client id. Required for reseller accounts only.",
    )

    @field_validator("client_id", mode="before")
    @classmethod
    def coerce_client_id(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not text.isdigit():
            raise ValueError("client_id must be a whole number")
        return text

    api_base_url: str = Field(
        default=PRODUCTION_API_BASE_URL,
        description=(
            "VoiceLink API base URL. Use https://app.voicelink.co.in/api for "
            "production or https://voicelinkuat.elisiontec.com/api for UAT."
        ),
    )
    # Phone numbers are owned by telephony_phone_numbers / factory attach.
    from_numbers: List[str] = Field(default_factory=list)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        cleaned = (value or "").strip().rstrip("/")
        if cleaned in ALLOWED_API_BASE_URLS:
            return cleaned
        raise ValueError(
            f"api_base_url must be {PRODUCTION_API_BASE_URL} (production) "
            f"or {UAT_API_BASE_URL} (UAT)"
        )
