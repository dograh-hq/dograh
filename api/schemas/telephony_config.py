"""Telephony configuration schemas.

Per-provider request/response classes live next to their providers in
``api/services/telephony/providers/<name>/config.py``. This module re-exports
them and assembles the discriminated union used by API routes.

Adding a new provider requires adding one import here.
"""

from typing import Annotated, Optional, Union

from pydantic import BaseModel, Field

from api.services.telephony.providers.ari.config import (
    ARIConfigurationRequest,
    ARIConfigurationResponse,
)
from api.services.telephony.providers.cloudonix.config import (
    CloudonixConfigurationRequest,
    CloudonixConfigurationResponse,
)
from api.services.telephony.providers.plivo.config import (
    PlivoConfigurationRequest,
    PlivoConfigurationResponse,
)
from api.services.telephony.providers.telnyx.config import (
    TelnyxConfigurationRequest,
    TelnyxConfigurationResponse,
)
from api.services.telephony.providers.twilio.config import (
    TwilioConfigurationRequest,
    TwilioConfigurationResponse,
)
from api.services.telephony.providers.vobiz.config import (
    VobizConfigurationRequest,
    VobizConfigurationResponse,
)
from api.services.telephony.providers.vonage.config import (
    VonageConfigurationRequest,
    VonageConfigurationResponse,
)

# Discriminated union for incoming save requests. Pydantic dispatches on the
# ``provider`` Literal field of each request class. Replaces the manual
# if/elif chains that used to live in routes/organization.py.
TelephonyConfigRequest = Annotated[
    Union[
        ARIConfigurationRequest,
        CloudonixConfigurationRequest,
        PlivoConfigurationRequest,
        TelnyxConfigurationRequest,
        TwilioConfigurationRequest,
        VobizConfigurationRequest,
        VonageConfigurationRequest,
    ],
    Field(discriminator="provider"),
]


class TelephonyConfigurationResponse(BaseModel):
    """Top-level telephony configuration response.

    Keeps the per-provider field shape that the UI client depends on. When
    the UI moves to metadata-driven forms, this can be replaced with a
    flat discriminated union.
    """

    twilio: Optional[TwilioConfigurationResponse] = None
    plivo: Optional[PlivoConfigurationResponse] = None
    vonage: Optional[VonageConfigurationResponse] = None
    vobiz: Optional[VobizConfigurationResponse] = None
    cloudonix: Optional[CloudonixConfigurationResponse] = None
    ari: Optional[ARIConfigurationResponse] = None
    telnyx: Optional[TelnyxConfigurationResponse] = None


__all__ = [
    "ARIConfigurationRequest",
    "ARIConfigurationResponse",
    "CloudonixConfigurationRequest",
    "CloudonixConfigurationResponse",
    "PlivoConfigurationRequest",
    "PlivoConfigurationResponse",
    "TelephonyConfigRequest",
    "TelephonyConfigurationResponse",
    "TelnyxConfigurationRequest",
    "TelnyxConfigurationResponse",
    "TwilioConfigurationRequest",
    "TwilioConfigurationResponse",
    "VobizConfigurationRequest",
    "VobizConfigurationResponse",
    "VonageConfigurationRequest",
    "VonageConfigurationResponse",
]
