"""Shared uniqueness guard for the inbound routing key.

Inbound dispatch resolves an incoming call by matching
``(provider, credentials[account_id_field], address_normalized)`` — see
``TelephonyPhoneNumberClient.find_inbound_route_by_account``. No database
constraint spans those three values (the account id lives in a JSON column on
``telephony_configurations``, the address on ``telephony_phone_numbers``), so
the tuple stays unique only because every write path that can produce one calls
``assert_no_inbound_routing_conflict`` first.

Two operations can create or change a component of the key:

* adding a phone number to a configuration — introduces an ``address_normalized``
* changing a configuration's credentials — changes the ``account_id``, which
  every one of its phone numbers shares, moving all their keys at once

Creating a configuration cannot: a new row owns no phone numbers, and
``from_numbers`` on the create payload is discarded. Its first conflict, if any,
surfaces when a number is added. Updating a phone number cannot either: address
and configuration are both immutable there.

Any new caller of ``create_phone_number`` or ``update_telephony_configuration``
must call this module first — the invariant is enforced here and nowhere else.
"""

from typing import Any, Mapping, Optional, Sequence

from api.db import db_client as default_db_client
from api.services.telephony import registry
from api.utils.telephony_address import normalize_telephony_address


class InboundRoutingConflictError(ValueError):
    """Raised when a save would leave an inbound routing key ambiguous.

    Carries the conflicting registration so callers can render a message that
    distinguishes "you already have this" from "somebody else does" without
    leaking the other organization's identity.
    """

    def __init__(
        self,
        *,
        address: str,
        configuration_name: str,
        same_organization: bool,
    ) -> None:
        self.address = address
        self.configuration_name = configuration_name
        self.same_organization = same_organization
        scope = (
            f"telephony configuration '{configuration_name}'"
            if same_organization
            else "another organization using the same provider account"
        )
        super().__init__(
            f"Phone number {address} is already registered under {scope}. "
            f"Inbound calls cannot be uniquely routed when the same number is "
            f"configured against the same provider account in more than one place."
        )


def canonical_address(address: str, country_hint: Optional[str] = None) -> str:
    """The stored ``address_normalized`` form of a raw address.

    Callers holding a raw user-supplied address pass it through here before
    ``assert_no_inbound_routing_conflict``; callers reading existing rows already
    have the canonical value and pass it straight through. Raises ``ValueError``
    for an address the normalizer cannot parse.
    """
    return normalize_telephony_address(address, country_hint=country_hint).canonical


def routing_account_id(
    provider: str, credentials: Optional[Mapping[str, Any]]
) -> Optional[str]:
    """The credential value inbound dispatch matches this provider's calls on.

    ``None`` when the provider has no account concept (ARI, whose inbound path
    is keyed differently) or when the credential has not been filled in yet.
    """
    spec = registry.get_optional(provider)
    if spec is None or not spec.account_id_credential_field:
        return None
    return (credentials or {}).get(spec.account_id_credential_field) or None


async def assert_no_inbound_routing_conflict(
    *,
    provider: str,
    credentials: Optional[Mapping[str, Any]],
    addresses: Sequence[str],
    organization_id: int,
    previous_credentials: Optional[Mapping[str, Any]] = None,
    exclude_configuration_id: Optional[int] = None,
    db: Any = default_db_client,
) -> None:
    """Raise ``InboundRoutingConflictError`` if this save would make a routing
    key ambiguous.

    ``addresses`` are canonical ``address_normalized`` values — the one being
    added, or every one an existing configuration already owns when its account
    id is changing. ``exclude_configuration_id`` keeps a configuration from
    conflicting with itself on update.

    ``previous_credentials``, when given, makes this a no-op if the routing
    account id is unchanged. That matters for updates: a rename or a secret
    rotation must not be blocked by a collision that already exists in the data
    and that this save does nothing to worsen.
    """
    spec = registry.get_optional(provider)
    if spec is None or not spec.account_id_credential_field:
        return

    account_id = routing_account_id(provider, credentials)
    if not account_id:
        return

    if previous_credentials is not None and account_id == routing_account_id(
        provider, previous_credentials
    ):
        return

    if not addresses:
        return

    conflicts = await db.find_inbound_routing_conflicts(
        provider=provider,
        account_id_field=spec.account_id_credential_field,
        account_id=account_id,
        addresses_normalized=list(addresses),
        exclude_configuration_id=exclude_configuration_id,
    )
    if not conflicts:
        return

    config, phone_number = conflicts[0]
    raise InboundRoutingConflictError(
        address=phone_number.address,
        configuration_name=config.name,
        same_organization=config.organization_id == organization_id,
    )
