"""Provider-agnostic WhatsApp messaging interface.

A new BSP (Gupshup, Wati, Meta Cloud API) only needs to implement send_template().
Adapters are fire-and-forget: a 2xx from the provider means "submitted", not
"delivered" — delivery status comes from provider webhooks, out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass
class WhatsAppSendResult:
    ok: bool
    detail: str
    provider_message_id: Optional[str] = None


class WhatsAppProvider(Protocol):
    name: str

    async def send_template(
        self,
        *,
        to: str,
        campaign_name: str,
        template_params: List[str],
        sender_name: str,
        media_url: Optional[str] = None,
        media_filename: Optional[str] = None,
    ) -> WhatsAppSendResult: ...


def normalize_destination(raw: str) -> str:
    """Digits-only phone for WhatsApp BSPs (strip +, spaces, dashes)."""
    return "".join(ch for ch in (raw or "") if ch.isdigit())
