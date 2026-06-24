"""Provider-agnostic CRM sync seam (mirrors api/services/whatsapp/base.py).

A CRM adapter takes one CallLog (built from a completed workflow run) and (1) upserts
the contact matched by phone and (2) logs a call activity/note. Adapters: gohighlevel
first; leadsquared / kylas / hubspot slot in behind the same protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class CallLog:
    """Everything a CRM needs about one completed call (provider-agnostic)."""

    phone: str
    name: str = ""
    email: str = ""
    disposition: str = ""
    duration_seconds: int = 0
    recording_url: str = ""
    transcript_url: str = ""
    summary: str = ""
    sentiment: str = ""
    quality_score: Optional[float] = None
    direction: str = "OUTBOUND"
    external_id: str = ""  # workflow_run id — dedupe / idempotency key
    extra: dict = field(default_factory=dict)  # gathered context (PTP amount, etc.)


@dataclass
class CRMSyncResult:
    ok: bool
    detail: str = ""
    contact_id: Optional[str] = None


class CRMProvider(Protocol):
    name: str

    async def sync_call(self, call: CallLog) -> CRMSyncResult:
        """Upsert the contact + log the call. Must not raise — return a result."""
        ...


def normalize_phone(raw: str, default_country: str = "91") -> str:
    """Best-effort E.164 (+CC...). Bare 10-digit numbers assume India (+91)."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:  # local Indian mobile
        return f"+{default_country}{digits}"
    if digits.startswith(default_country):
        return "+" + digits
    return "+" + digits


def render_call_note(call: CallLog) -> str:
    """Human-readable call summary written as the CRM note/activity body."""
    lines = ["📞 AI voice call"]
    if call.disposition:
        lines.append(f"Outcome: {call.disposition}")
    if call.duration_seconds:
        lines.append(f"Duration: {call.duration_seconds}s")
    if call.sentiment:
        lines.append(f"Sentiment: {call.sentiment}")
    if call.quality_score is not None:
        lines.append(f"Quality: {call.quality_score}")
    if call.summary:
        lines.append(f"\nSummary: {call.summary}")
    if call.recording_url:
        lines.append(f"Recording: {call.recording_url}")
    if call.transcript_url:
        lines.append(f"Transcript: {call.transcript_url}")
    for k, v in (call.extra or {}).items():
        if v not in (None, "", {}):
            lines.append(f"{k}: {v}")
    return "\n".join(lines)
