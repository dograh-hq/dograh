"""Per-org post-call CRM sync config.

Stored as a single JSON value under OrganizationConfigurationKey.CRM_PROVIDERS.
Provider-agnostic: `provider` selects the adapter (gohighlevel first; leadsquared/
kylas/hubspot follow). After each qualifying call the platform upserts the contact
(matched by phone) and logs a call activity/note with disposition, duration,
recording/transcript links, sentiment, and summary.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class CRMConfig(BaseModel):
    enabled: bool = False
    provider: str = "gohighlevel"  # gohighlevel | leadsquared | kylas | hubspot (future)
    api_key: str = ""  # sensitive — masked on read, encrypted at rest
    # GoHighLevel sub-account (Location) id; data-center/host for region-bound CRMs.
    location_id: str = ""
    region_host: str = ""  # e.g. LeadSquared api-inXX host (provider-specific)
    # Empty = log for any disposition; else only these mapped dispositions.
    trigger_dispositions: List[str] = Field(default_factory=list)
    # Empty = sync regardless of sentiment; else only when overall_sentiment matches
    # one of these (case-insensitive substring), e.g. ["interested", "positive"].
    trigger_sentiments: List[str] = Field(default_factory=list)
    # Only sync if the call lasted at least this many seconds (0 = no gate).
    min_call_seconds: int = 0


class CRMConfigResponse(BaseModel):
    config: Optional[CRMConfig] = None


class CRMTestRequest(BaseModel):
    phone: str = ""  # optional phone to upsert as a connectivity probe
