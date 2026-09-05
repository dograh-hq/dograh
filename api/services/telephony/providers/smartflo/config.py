"""Smartflo telephony configuration schemas."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SmartfloConfigurationRequest(BaseModel):
    """Request schema for Smartflo configuration."""

    provider: Literal["smartflo"] = Field(default="smartflo")
    click_to_call_api_key: str = Field(
        ...,
        description="Smartflo Click-to-Call Support API Key",
    )
    smartflo_jwt_token: Optional[str] = Field(
        default=None,
        description="Smartflo Bearer JWT Token for authorization",
    )
    smartflo_did_number: Optional[str] = Field(
        default=None,
        description="Smartflo Caller ID / DID number (e.g. 91XXXXXXXXXX)",
    )
    smartflo_api_domain: Optional[str] = Field(
        default="https://api-smartflo.tatateleservices.com",
        description="Smartflo API Domain (default: https://api-smartflo.tatateleservices.com)",
    )
