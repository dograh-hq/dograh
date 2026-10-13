"""WhatsApp telephony configuration schema."""

from typing import Literal

from pydantic import BaseModel, Field


class WhatsAppConfigurationRequest(BaseModel):
    """Credentials for one WhatsApp Business phone number on the Cloud API.

    The webhook verify token is deliberately not declared here: it is minted
    by the provider's save hook and carried forward on update, never read from
    a request (see ``_preprocess_credentials_on_save`` in the package).
    """

    provider: Literal["whatsapp"] = Field(default="whatsapp")
    access_token: str = Field(
        ..., min_length=1, description="System user access token from Meta"
    )
    phone_number_id: str = Field(
        ..., min_length=1, description="Phone Number ID from the app's API Setup page"
    )
    app_secret: str = Field(
        ..., min_length=1, description="App secret used to verify webhook signatures"
    )
