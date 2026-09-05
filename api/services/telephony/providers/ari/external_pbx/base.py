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
    # How long to give the PBX to drop the customer leg itself after it accepts
    # a hangup, before Dograh deletes its own channel. The PBX placed the call
    # and is still accounting for it, so it -- not Asterisk -- should send the
    # BYE; deleting the channel first makes Dograh look like it abandoned a live
    # call. Zero keeps the immediate teardown for a PBX whose hangup API is
    # synchronous (or that does not hang up the leg at all).
    hangup_bye_wait_seconds: float = 0.0

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

    async def apply_do_not_call(
        self, identity: Mapping[str, Any], disposition: str | None
    ) -> ExternalPBXResult | None:
        """Suppress the customer's number if ``disposition`` is a DNC request.

        Returns ``None`` when the disposition is not one, so the caller does not
        need to know which of a PBX's dispositions carry that meaning. Recording
        the outcome on the call is not enough on its own: a lead-level status
        stops the current campaign redialing, while suppression has to hold
        wherever else the number appears.
        """
        return None

    def disposition_fields(self, disposition: str | None) -> dict[str, str]:
        """Fields that record ``disposition`` on the PBX's own copy of the call.

        Separate from the workflow's ``external_pbx_field_mappings`` because the
        outcome of a call is not per-workflow configuration: a PBX that models
        dispositions natively should record every call's, and one that does not
        returns nothing here. Callers merge the workflow mappings over the top,
        so an explicit mapping still wins.
        """
        return {}
