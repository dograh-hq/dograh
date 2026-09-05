"""Telephony configuration schemas.

Per-provider request/response classes live next to their providers in
``api/services/telephony/providers/<name>/config.py``. This module re-exports
them and assembles the discriminated union used by API routes.

Adding a new provider requires adding one import here.
"""

from datetime import datetime
from typing import Annotated, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from api.services.telephony.base import SIPConnectivityDetails
from api.services.telephony.providers.ari.config import (
    ARIConfigurationRequest,
)
from api.services.telephony.providers.cloudonix.config import (
    CloudonixConfigurationRequest,
)
from api.services.telephony.providers.plivo.config import (
    PlivoConfigurationRequest,
)
from api.services.telephony.providers.telnyx.config import (
    TelnyxConfigurationRequest,
)
from api.services.telephony.providers.twilio.config import (
    TwilioConfigurationRequest,
)
from api.services.telephony.providers.vobiz.config import (
    VobizConfigurationRequest,
)
from api.services.telephony.providers.vonage.config import (
    VonageConfigurationRequest,
)
from api.services.telephony.registry import (
    ProviderConnectivity,
    ProviderSetupChecklist,
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
    # Denormalized from the provider registry so clients can tell a carrier
    # account apart from a bring-your-own-SIP connection without a second
    # request and without a hardcoded provider list of their own.
    connectivity: ProviderConnectivity = "api"
    is_default_outbound: bool
    inactive: bool = False
    inactive_since: datetime | None = None
    inactive_reason: str | None = None
    phone_number_count: int = 0
    # Whether this configuration can actually place an outbound call, as
    # reported by the provider's setup-checklist hook. Providers without one
    # are ready as soon as their credentials are stored, hence the default.
    is_ready_for_outbound: bool = True
    outbound_blocked_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class TrunkResponse(BaseModel):
    """One carrier path on a configuration.

    ``settings`` is the provider's own trunk schema (validated on write against
    ``ProviderSpec.trunk_settings_cls``). The provider-side identifier is
    Dograh's bookkeeping and is not exposed.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    enabled: bool
    settings: dict
    phone_number_count: int = 0
    created_at: datetime
    updated_at: datetime


class TrunkCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    settings: dict = Field(default_factory=dict)


class TrunkUpdateRequest(BaseModel):
    """Partial update — omitted fields keep their stored value."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    enabled: Optional[bool] = None
    settings: Optional[dict] = None


class TrunkListResponse(BaseModel):
    trunks: List[TrunkResponse]


class TelephonyConfigurationDetail(BaseModel):
    """Body of ``GET /telephony-configs/{id}`` — credentials are masked."""

    id: int
    name: str
    provider: str
    connectivity: ProviderConnectivity = "api"
    is_default_outbound: bool
    inactive: bool = False
    inactive_since: datetime | None = None
    inactive_reason: str | None = None
    credentials: dict
    sip_connectivity: SIPConnectivityDetails | None = None
    setup_checklist: ProviderSetupChecklist | None = None
    # Whether the provider's Dograh integration models trunks at all. Distinct
    # from ``trunks`` being empty, which is equally the state of a trunk-capable
    # configuration nobody has added one to yet — the UI needs to tell those
    # apart to know whether to offer the "add a trunk" affordance.
    supports_trunks: bool = False
    # Empty unless the provider's Dograh integration models trunks; the
    # call-control integrations route through the account itself.
    trunks: List[TrunkResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TelephonyConfigurationListResponse(BaseModel):
    configurations: List[TelephonyConfigurationListItem]


__all__ = [
    "ARIConfigurationRequest",
    "CloudonixConfigurationRequest",
    "PlivoConfigurationRequest",
    "TelephonyConfigRequest",
    "TrunkCreateRequest",
    "TrunkListResponse",
    "TrunkResponse",
    "TrunkUpdateRequest",
    "TelnyxConfigurationRequest",
    "TwilioConfigurationRequest",
    "VobizConfigurationRequest",
    "VonageConfigurationRequest",
]
