"""VoiceLink reseller KYC service.

Wraps the VoiceLink reseller KYC API (PAN / Aadhaar / GST verification
required for Indian telephony) behind a small aiohttp client that uses the
reseller credentials from the environment. The optional per-organization
``client_id`` is resolved from the org's VoiceLink telephony configuration
credentials (JSONB) — when absent, KYC calls act on the reseller's own KYC.
"""

from .client import (
    VoiceLinkKycClient,
    VoiceLinkKycError,
    get_kyc_client,
    resolve_org_voicelink_client_id,
)

__all__ = [
    "VoiceLinkKycClient",
    "VoiceLinkKycError",
    "get_kyc_client",
    "resolve_org_voicelink_client_id",
]
