"""Contracts for PBXs that hand a customer leg to Dograh through Asterisk."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

HeaderReader = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class ExternalPBXResult:
    ok: bool
    action: str
    message: str


class ExternalPBXAdapter(ABC):
    """PBX-specific operations; ARI continues to own only Dograh's local leg."""

    type: str

    @abstractmethod
    async def capture_call_identity(
        self, read_header: HeaderReader
    ) -> dict[str, str] | None:
        """Read a stable upstream-call identity from inbound SIP headers."""

    @abstractmethod
    async def hangup(self, identity: Mapping[str, str]) -> ExternalPBXResult:
        """Hang up the customer leg owned by the external PBX."""

    @abstractmethod
    async def transfer(
        self, identity: Mapping[str, str], destination: str
    ) -> ExternalPBXResult:
        """Transfer the customer leg to a PBX-native destination."""

    @abstractmethod
    async def update_fields(
        self, identity: Mapping[str, str], fields: Mapping[str, str]
    ) -> ExternalPBXResult:
        """Update provider-native fields associated with the call."""
