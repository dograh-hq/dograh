"""Request/response schemas for the phone-number CRUD endpoints."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class PhoneNumberCreateRequest(BaseModel):
    """Create a new phone number under a telephony configuration.

    ``address_normalized`` and ``address_type`` are computed server-side from
    ``address`` (and ``country_code`` if PSTN). ``address`` itself is stored
    verbatim for display.
    """

    address: str = Field(..., min_length=1, max_length=255)
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    label: Optional[str] = Field(default=None, max_length=64)
    inbound_workflow_id: Optional[int] = None
    is_active: bool = True
    is_default_caller_id: bool = False
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)


class PhoneNumberUpdateRequest(BaseModel):
    """Partial update. ``address`` is intentionally immutable — to change a
    number, delete the row and create a new one."""

    label: Optional[str] = Field(default=None, max_length=64)
    inbound_workflow_id: Optional[int] = None
    # Set to true to clear inbound_workflow_id (FK is otherwise non-nullable
    # via the partial-update pattern).
    clear_inbound_workflow: bool = False
    is_active: Optional[bool] = None
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    extra_metadata: Optional[Dict[str, Any]] = None


class ProviderSyncStatus(BaseModel):
    """Result of pushing a phone-number change to the upstream provider.

    Returned alongside create/update responses when the route attempted to
    sync inbound webhook configuration. ``ok=False`` is a warning, not a
    fatal error — the DB write succeeded.
    """

    ok: bool
    message: Optional[str] = None


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telephony_configuration_id: int
    address: str
    address_normalized: str
    address_type: str
    country_code: Optional[str] = None
    label: Optional[str] = None
    inbound_workflow_id: Optional[int] = None
    inbound_workflow_name: Optional[str] = None
    is_active: bool
    is_default_caller_id: bool
    extra_metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # Only set on create/update responses when the route attempted a
    # provider-side sync (e.g. setting Twilio's VoiceUrl). Omitted on reads.
    provider_sync: Optional[ProviderSyncStatus] = None


class PhoneNumberListResponse(BaseModel):
    phone_numbers: list[PhoneNumberResponse]
