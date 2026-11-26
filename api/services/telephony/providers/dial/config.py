"""Dial telephony configuration schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class DialConfigurationRequest(BaseModel):
    """Request schema for Dial configuration.

    The API key is the only thing the customer supplies. Everything else the
    integration needs (the Dial account id, the Self-Hosted audio signing
    secret, the call-status webhook and its secret) is resolved from the
    account on save and stored as server-managed credentials.
    """

    provider: Literal["dial"] = Field(default="dial")
    api_key: str = Field(..., description="Dial API key (starts with sk_live_)")
