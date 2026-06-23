"""Pydantic models for the per-org WhatsApp post-call messaging config.

Stored as a single JSON value under OrganizationConfigurationKey.WHATSAPP_PROVIDERS.
Provider-agnostic: `provider` selects the adapter (aisensy first; gupshup/wati/meta
can follow). `template_params` are ordered strings that may contain {{tokens}}
(e.g. {{called_number}}, {{recording_url}}, {{disposition}}, {{var.name}}) which the
post-call sender substitutes from the call context before sending.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    provider: str = "aisensy"  # aisensy | gupshup | wati | meta (future)
    api_key: str = ""  # sensitive — masked on read
    # AiSensy: `userName` (sender/brand label) + `campaignName` (binds an approved template)
    sender_name: str = ""
    campaign_name: str = ""  # AiSensy campaignName / template reference
    # Ordered positional params filling {{1}},{{2}}... — values may contain {{tokens}}.
    template_params: List[str] = Field(default_factory=list)
    # Empty = send for any disposition; else only these mapped dispositions.
    trigger_dispositions: List[str] = Field(default_factory=list)
    # Only send if the call lasted at least this many seconds (0 = no gate).
    min_call_seconds: int = 0
    # Optional header media (must resolve to a publicly reachable URL).
    media_url: Optional[str] = None
    media_filename: Optional[str] = None


class WhatsAppConfigResponse(BaseModel):
    config: Optional[WhatsAppConfig] = None


class WhatsAppTestRequest(BaseModel):
    destination: str  # phone number to send the test to
