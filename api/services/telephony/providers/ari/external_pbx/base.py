"""Contracts for PBXs that hand a customer leg to Dograh through Asterisk."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

HeaderReader = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class ExternalPBXResult:
    ok: bool
    action: str
    message: str


class ExternalPBXAdapter(ABC):
    """PBX-specific operations; ARI continues to own only Dograh's local leg."""

    type: str
    # SIP header namespace this PBX attaches call identity and lead data under.
    # Enumerating it lists the fields a deployment actually sends, which is what
    # the "Lead Fields To Capture" setting is populated from.
    header_prefix: str = ""

    @abstractmethod
    async def capture_call_identity(
        self, read_header: HeaderReader, lead_fields: Sequence[str] = ()
    ) -> dict[str, Any] | None:
        """Read a stable upstream-call identity from inbound SIP headers.

        Every header costs one ARI round trip on the inbound call-setup path,
        so the caller passes the exact ``lead_fields`` the workflow configured
        instead of the adapter enumerating whatever the PBX happens to attach.
        """

    @abstractmethod
    async def hangup(self, identity: Mapping[str, Any]) -> ExternalPBXResult:
        """Hang up the customer leg owned by the external PBX."""

    @abstractmethod
    async def transfer(
        self, identity: Mapping[str, Any], destination: str
    ) -> ExternalPBXResult:
        """Transfer the customer leg to a PBX-native destination."""

    @abstractmethod
    async def update_fields(
        self, identity: Mapping[str, Any], fields: Mapping[str, str]
    ) -> ExternalPBXResult:
        """Update provider-native fields associated with the call."""
