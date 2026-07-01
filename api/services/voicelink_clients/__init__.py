"""VoiceLink reseller client-management service.

Creates VoiceLink "clients" (sub-accounts under our reseller account) via
``POST /v1/reseller/client/create``. Provisioning runs best-effort at
local-auth signup and can be retried from the admin Clients view; the
outcome is stored on the organization (``voicelink_client_id`` /
``voicelink_username`` / ``voicelink_status`` / ``voicelink_error``).
"""

from .client import (
    VoiceLinkClientError,
    VoiceLinkClientsClient,
    get_voicelink_clients_client,
)
from .service import (
    derive_username,
    ensure_voicelink_client,
    generate_client_password,
    provision_voicelink_client,
    provision_voicelink_client_for_signup,
    resolve_org_owner,
    split_signup_name,
)

__all__ = [
    "VoiceLinkClientError",
    "VoiceLinkClientsClient",
    "get_voicelink_clients_client",
    "derive_username",
    "ensure_voicelink_client",
    "generate_client_password",
    "provision_voicelink_client",
    "provision_voicelink_client_for_signup",
    "resolve_org_owner",
    "split_signup_name",
]
