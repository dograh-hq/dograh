"""Telephony configuration schemas.

Per-provider request/response classes live next to their providers in
``api/services/telephony/providers/<name>/config.py``. This module re-exports
them and assembles the discriminated union used by API routes.

Adding a new provider requires adding one import here.
"""

from datetime import datetime
from typing import Annotated, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

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


# ---------------------------------------------------------------------------
# Multi-config CRUD schemas
# ---------------------------------------------------------------------------


class TelephonyConfigurationCreateRequest(BaseModel):
    """Body for ``POST /telephony-configs``.

    ``config`` carries the provider-specific credential fields (the same
    discriminated union used by the legacy single-config endpoint). Any
    ``from_numbers`` on the inner config are ignored — phone numbers are
    managed via the dedicated phone-numbers endpoints.
    """

    name: str = Field(..., min_length=1, max_length=64)
    is_default_outbound: bool = False
    config: TelephonyConfigRequest


class TelephonyConfigurationUpdateRequest(BaseModel):
    """Body for ``PUT /telephony-configs/{id}``. Partial update."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    config: Optional[TelephonyConfigRequest] = None


class TelephonyConfigurationListItem(BaseModel):
    """One row in ``GET /telephony-configs``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    is_default_outbound: bool
    phone_number_count: int = 0
    created_at: datetime
    updated_at: datetime


class TelephonyConfigurationDetail(BaseModel):
    """Body of ``GET /telephony-configs/{id}`` — credentials are masked."""

    id: int
    name: str
    provider: str
    is_default_outbound: bool
    credentials: dict
    created_at: datetime
    updated_at: datetime


class TelephonyConfigurationListResponse(BaseModel):
    configurations: List[TelephonyConfigurationListItem]


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
