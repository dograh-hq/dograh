"""Import a provider account's phone numbers into a telephony configuration.

The "refresh numbers" counterpart to typing a number in: the provider is asked
what its account holds, and each number becomes a ``telephony_phone_numbers``
row on the configuration, or refreshes the row it already has. Provider
details live under one key of the row's metadata and never touch the rest. Rows
are never removed here. A number the provider stops reporting is handed back as missing
for the operator to decide on, because a listing gap must not silence a live
number.
"""

from dataclasses import dataclass, field
from typing import Any, List

from api.db import db_client as default_db_client
from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
from api.db.telephony_phone_number_client import (
    PROVIDER_METADATA_KEY,
    TelephonyPhoneNumberConflictError,
)
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider
from api.services.telephony.inbound_routing import (
    InboundRoutingConflictError,
    assert_no_inbound_routing_conflict,
)
from api.utils.telephony_address import normalize_telephony_address


class PhoneNumberSyncUnsupportedError(Exception):
    """The provider has no account inventory to import from."""


@dataclass(frozen=True)
class SkippedNumber:
    address: str
    reason: str


@dataclass
class PhoneNumberSyncOutcome:
    created: List[TelephonyPhoneNumberModel] = field(default_factory=list)
    updated: List[TelephonyPhoneNumberModel] = field(default_factory=list)
    skipped: List[SkippedNumber] = field(default_factory=list)
    missing_at_provider: List[TelephonyPhoneNumberModel] = field(default_factory=list)


async def sync_phone_numbers_from_provider(
    *,
    configuration: TelephonyConfigurationModel,
    organization_id: int,
    provider: TelephonyProvider,
    db: Any = default_db_client,
) -> PhoneNumberSyncOutcome:
    """Bring the configuration's rows up to date with the provider's inventory.

    ``ProviderPhoneNumberLookupError`` from the provider propagates: the
    caller owns the HTTP mapping, as it does for the manual add route.
    """
    spec = registry.get_optional(configuration.provider)
    if spec is None or not spec.supports_phone_number_sync:
        raise PhoneNumberSyncUnsupportedError(
            f"{configuration.provider} cannot list the numbers its account holds"
        )

    inventory = await provider.list_provider_phone_numbers()
    existing = await db.list_phone_numbers_for_config(configuration.id)
    by_address = {row.address_normalized: row for row in existing}

    outcome = PhoneNumberSyncOutcome()
    seen: set[str] = set()
    for number in inventory:
        canonical = normalize_telephony_address(number.address).canonical
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)

        row = by_address.get(canonical)
        if row is not None:
            # Applied against the row's live state under a lock, so an
            # operator edit made since the listing above is not undone.
            refreshed = await db.refresh_phone_number_from_provider(
                row.id,
                configuration.id,
                provider_metadata=dict(number.metadata),
                label=number.label,
            )
            if refreshed is not None:
                outcome.updated.append(refreshed)
            continue

        # The inbound routing key must stay unambiguous across every org.
        try:
            await assert_no_inbound_routing_conflict(
                provider=configuration.provider,
                credentials=configuration.credentials,
                addresses=[canonical],
                organization_id=organization_id,
                db=db,
            )
        except InboundRoutingConflictError as e:
            outcome.skipped.append(SkippedNumber(address=number.address, reason=str(e)))
            continue

        try:
            created = await db.create_phone_number(
                organization_id=organization_id,
                telephony_configuration_id=configuration.id,
                address=number.address,
                label=number.label,
                extra_metadata={PROVIDER_METADATA_KEY: dict(number.metadata)},
            )
        except TelephonyPhoneNumberConflictError:
            outcome.skipped.append(
                SkippedNumber(
                    address=number.address,
                    reason=(
                        "already registered on another configuration in this "
                        "organization"
                    ),
                )
            )
            continue
        outcome.created.append(created)

    outcome.missing_at_provider = [
        row for row in existing if row.address_normalized not in seen
    ]
    return outcome
