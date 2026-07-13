"""LiveKit SIP telephony configuration schemas."""

from typing import Literal

from pydantic import BaseModel


class LiveKitSipConfigurationRequest(BaseModel):
    """Incoming save request for LiveKit SIP provider."""

    provider: Literal["livekit_sip"] = "livekit_sip"
    sip_trunk_id: str = ""
    api_key: str = ""
    api_secret: str = ""


class LiveKitSipConfigurationResponse(BaseModel):
    """Masked response for LiveKit SIP provider."""

    provider: Literal["livekit_sip"] = "livekit_sip"
    sip_trunk_id: str = ""
