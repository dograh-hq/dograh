"""Database access for telephony configurations.

Each row represents one provider account that an organization has connected
(e.g. "Twilio US prod", "Vobiz IN sandbox"). Replaces the single-row-per-org
``OrganizationConfiguration(TELEPHONY_CONFIGURATION)`` storage.
"""

import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CampaignModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WhatsAppCallPermissionModel,
)
from api.utils.telephony_address import normalize_telephony_address

# A recipient that carries no letters and no SIP punctuation is a dialable
# number however its separators are spelled, so its digits alone identify it.
_LETTERS_OR_SIP_RE = re.compile(r"[A-Za-z@:]")


def _canonical_recipient_number(recipient_phone_number: str) -> str:
    """Reduce a recipient number to the single form permission rows are keyed by.

    uq_whatsapp_perm_config_recipient is a constraint on the stored string, so it
    only prevents duplicates if every writer stores the same spelling. Storing
    whatever the caller happened to pass would key the row on its arrival format:
    a row written as "+44 7123 456789" is invisible to a later lookup for
    "+447123456789", which then inserts a second row for the same recipient.
    This form is always in _get_recipient_number_candidates, so existing lookups
    keep finding it.

    ``normalize_telephony_address`` only strips spaces, hyphens and parentheses
    before deciding whether an input is a phone number; anything else (dots,
    slashes, non-breaking spaces) leaves it classified as a SIP extension and
    canonicalized to the raw string, which would key the row on its arrival
    format after all. WhatsApp recipients are always PSTN numbers, so a
    letter-free input is reduced to its digits first and only then handed to
    the shared normalizer, which agrees with this form for every spelling it
    does understand.
    """
    raw = recipient_phone_number.strip()
    if not _LETTERS_OR_SIP_RE.search(raw):
        digits = re.sub(r"\D", "", raw)
        if digits:
            return f"+{digits}"
    try:
        return normalize_telephony_address(raw).canonical
    except Exception:
        digits = re.sub(r"\D", "", raw)
        return f"+{digits}" if digits else raw


def _get_recipient_number_candidates(recipient_phone_number: str) -> list[str]:
    """Every stored spelling a lookup for this recipient must be able to reach.

    The stored key itself leads the list: keying writes on one form only stops
    duplicate rows if readers search for that same form, so the two helpers are
    tied together here rather than left to agree by coincidence.
    """
    raw = recipient_phone_number.strip()
    digits = re.sub(r"\D", "", raw)
    canonical_key = _canonical_recipient_number(raw)
    candidates = [canonical_key, canonical_key.lstrip("+"), raw, raw.lstrip("+")]
    if digits:
        candidates.extend([digits, f"+{digits}"])
    try:
        canonical = normalize_telephony_address(raw).canonical
        candidates.extend([canonical, canonical.lstrip("+")])
    except Exception:
        pass
    return list(dict.fromkeys([c for c in candidates if c]))


async def _select_permission_row(session, query, key: str):
    """Pick one permission row, deterministically, and surface any duplicates.

    Two independent things can put more than one row on the same query: rows
    written before recipients were stored canonically may still exist under
    several spellings (the unique constraint can't merge them, since it
    constrains the stored string), and ``meta_message_id`` lookups are
    deliberately backed by a non-unique index since Meta can replay a wamid
    across rows. Either way, ordering by id makes every reader agree on the
    same row instead of taking an arbitrary one, so permission checks and
    webhook updates cannot drift onto different records for the same
    recipient or message.
    """
    result = await session.execute(query.order_by(WhatsAppCallPermissionModel.id))
    rows = list(result.scalars().all())
    if len(rows) > 1:
        logger.warning(
            f"[WhatsApp Permission] {len(rows)} rows share lookup key {key!r} "
            f"(ids={[r.id for r in rows]}); using {rows[0].id}."
        )
    return rows[0] if rows else None


class _Unset:
    """Marker for "the caller did not mention this field".

    Nullable permission columns carry meaning when they are empty: a revoked
    grant has no expiry and no grant time. ``None`` is therefore a value a
    caller needs to be able to write, which it cannot be if ``None`` also means
    "leave whatever is there". Fields default to this marker instead, so an
    omitted argument keeps the stored value and an explicit ``None`` clears it.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


def _supplied_or_none(value):
    """Read a maybe-omitted argument as the value a new row should start with."""
    return None if isinstance(value, _Unset) else value


def _apply_permission_updates(
    row: WhatsAppCallPermissionModel,
    *,
    status: str,
    now: datetime,
    phone_number_id=UNSET,
    permission_type=UNSET,
    meta_message_id=UNSET,
    expires_at=UNSET,
    granted_at=UNSET,
) -> None:
    """Write the supplied fields onto an existing permission row.

    Only fields the caller actually passed are touched, so a status-only
    update leaves the rest alone, while a field passed as ``None`` is cleared
    rather than silently retaining the previous grant's value.
    """
    row.status = status
    if not isinstance(phone_number_id, _Unset):
        row.phone_number_id = phone_number_id
    if not isinstance(permission_type, _Unset):
        row.permission_type = permission_type
    if not isinstance(meta_message_id, _Unset):
        row.meta_message_id = meta_message_id
    if not isinstance(expires_at, _Unset):
        row.expires_at = expires_at
    if not isinstance(granted_at, _Unset):
        row.granted_at = granted_at
    row.updated_at = now


async def _resolve_whatsapp_configs_by_phone_number_id(
    session, phone_number_id: str
) -> Dict[int, TelephonyConfigurationModel]:
    """Every active WhatsApp configuration a Meta phone_number_id can refer to.

    An id reaches us from two independent places that are not guaranteed to
    name the same tenant: the id a customer typed into a configuration's
    credentials, and the id recorded on a phone number attached to a
    configuration (WABA-level setups, where the configured id is the business
    account and Meta's webhooks carry one of the numbers underneath it). The
    same business connected by two organizations lands in both. Searching one
    source first and returning on a hit would silently pick a tenant, so both
    are searched and merged; callers decide what to do when more than one
    configuration comes back.
    """
    if not phone_number_id:
        return {}

    matches: Dict[int, TelephonyConfigurationModel] = {}

    credentials_stmt = select(TelephonyConfigurationModel).where(
        TelephonyConfigurationModel.provider == "whatsapp",
        TelephonyConfigurationModel.credentials.op("->>")("phone_number_id")
        == phone_number_id,
        TelephonyConfigurationModel.inactive.is_(False),
    )
    result = await session.execute(credentials_stmt)
    for row in result.scalars().all():
        matches[row.id] = row

    attached_stmt = (
        select(TelephonyConfigurationModel)
        .join(
            TelephonyPhoneNumberModel,
            TelephonyPhoneNumberModel.telephony_configuration_id
            == TelephonyConfigurationModel.id,
        )
        .where(
            TelephonyConfigurationModel.provider == "whatsapp",
            TelephonyConfigurationModel.inactive.is_(False),
            TelephonyPhoneNumberModel.is_active.is_(True),
            (
                TelephonyPhoneNumberModel.extra_metadata.op("->>")("phone_number_id")
                == phone_number_id
            )
            | (
                TelephonyPhoneNumberModel.extra_metadata.op("->>")(
                    "meta_phone_number_id"
                )
                == phone_number_id
            ),
        )
    )
    result_attached = await session.execute(attached_stmt)
    for row in result_attached.scalars().all():
        matches[row.id] = row

    return matches


def _log_ambiguous_phone_number_id(
    phone_number_id: str,
    configs: Dict[int, TelephonyConfigurationModel],
    action: str,
) -> None:
    ids = ", ".join(
        f"{cfg_id} (org {cfg.organization_id})" for cfg_id, cfg in configs.items()
    )
    logger.error(
        f"[WhatsApp] Ambiguous phone_number_id={phone_number_id!r}: matches "
        f"{len(configs)} active configurations ({ids}). {action}"
    )


class TelephonyConfigurationInUseError(Exception):
    """Raised when deleting a config that is still referenced by a campaign."""


class TelephonyConfigurationConflictError(Exception):
    """Raised when a telephony configuration violates a DB constraint."""


class TelephonyConfigurationClient(BaseDBClient):
    async def list_telephony_configurations(
        self, organization_id: int
    ) -> List[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(TelephonyConfigurationModel.organization_id == organization_id)
                .order_by(TelephonyConfigurationModel.created_at)
            )
            return list(result.scalars().all())

    async def list_outbound_telephony_configuration_candidates(
        self, organization_id: int
    ) -> List[TelephonyConfigurationModel]:
        """Active outbound candidates, with an explicit default considered first."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.inactive.is_(False),
                )
                .order_by(
                    TelephonyConfigurationModel.is_default_outbound.desc(),
                    TelephonyConfigurationModel.created_at,
                    TelephonyConfigurationModel.id,
                )
            )
            return list(result.scalars().all())

    async def get_telephony_configuration(
        self, config_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyConfigurationModel, config_id)

    async def get_telephony_configuration_for_org(
        self,
        config_id: int,
        organization_id: int,
        active_only: bool = True,
    ) -> Optional[TelephonyConfigurationModel]:
        """Lookup scoped to an org, excluding parked configs by default.

        Management flows that need to display, repair, or reactivate a parked
        row must opt in with ``active_only=False``.
        """
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.id == config_id,
                TelephonyConfigurationModel.organization_id == organization_id,
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return result.scalars().first()

    async def get_default_telephony_configuration(
        self, organization_id: int, active_only: bool = True
    ) -> Optional[TelephonyConfigurationModel]:
        """Return the default outbound config, if it is usable for routing."""
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_default_outbound.is_(True),
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return result.scalars().first()

    async def list_telephony_configurations_by_provider(
        self, organization_id: int, provider: str, active_only: bool = True
    ) -> List[TelephonyConfigurationModel]:
        """List provider configs usable for inbound matching by default."""
        async with self.async_session() as session:
            query = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.provider == provider,
            )
            if active_only:
                query = query.where(TelephonyConfigurationModel.inactive.is_(False))
            result = await session.execute(query)
            return list(result.scalars().all())

    async def count_telnyx_configs_missing_webhook_public_key(
        self, organization_id: int
    ) -> int:
        """Count Telnyx configs in this org with no webhook_public_key in credentials.

        Used by the org-warnings endpoint to surface a UI nudge until customers
        paste their portal-issued public key.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "telnyx",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "webhook_public_key"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def count_vonage_configs_missing_signature_secret(
        self, organization_id: int
    ) -> int:
        """Count Vonage configs in this org with no signature_secret."""
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.provider == "vonage",
                    (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        ).is_(None)
                    )
                    | (
                        TelephonyConfigurationModel.credentials.op("->>")(
                            "signature_secret"
                        )
                        == ""
                    ),
                )
            )
            return int(result.scalar() or 0)

    async def list_active_telephony_configurations_by_provider(
        self, provider: str
    ) -> List[TelephonyConfigurationModel]:
        """List the non-deactivated configs of a given provider, across all orgs.

        Used by background workers like the ARI manager that maintain
        long-lived connections per config row, independent of any one org.
        Deactivated rows stay excluded until someone reactivates them.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyConfigurationModel).where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.inactive.is_(False),
                )
            )
            return list(result.scalars().all())

    async def get_whatsapp_configuration_by_phone_number_id(
        self, phone_number_id: str
    ) -> Optional[TelephonyConfigurationModel]:
        """Look up an active WhatsApp telephony configuration by phone_number_id.

        Matches either the phone_number_id stored directly in configuration
        credentials or the phone_number_id stored in attached active phone
        number extra_metadata (supporting WABA-level account setups). Both
        sources are evaluated: an id that resolves through one source on one
        organization and through the other source on a different organization
        is ambiguous, and answering with whichever source was consulted first
        would route a call into an arbitrary tenant. An ambiguous id returns
        None so the caller rejects the call instead.
        """
        async with self.async_session() as session:
            configs = await _resolve_whatsapp_configs_by_phone_number_id(
                session, phone_number_id
            )
        if len(configs) > 1:
            _log_ambiguous_phone_number_id(
                phone_number_id,
                configs,
                "Rejecting the call to prevent wrong-tenant routing.",
            )
            return None  # caller will reject the call
        return next(iter(configs.values()), None)

    async def get_active_whatsapp_configurations(
        self,
    ) -> list[TelephonyConfigurationModel]:
        """Fetch all active WhatsApp telephony configurations."""
        async with self.async_session() as session:
            stmt = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.provider == "whatsapp",
                TelephonyConfigurationModel.inactive.is_(False),
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_whatsapp_configuration_by_verify_token(
        self, verify_token: str
    ) -> Optional[TelephonyConfigurationModel]:
        """Look up the active WhatsApp config matching a webhook verify token.

        Returns None when more than one matches. The verify token is the only
        thing Meta's handshake carries - there is no app or phone-number id on
        that request - so a token two tenants happen to share identifies
        neither, and taking the first row would complete one tenant's
        subscription against the other's configuration. Nothing forces these
        tokens to be unique, so the ambiguity is resolved by refusing it.
        """
        async with self.async_session() as session:
            stmt = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.provider == "whatsapp",
                TelephonyConfigurationModel.credentials.op("->>")(
                    "webhook_verify_token"
                )
                == verify_token,
                TelephonyConfigurationModel.inactive.is_(False),
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            if len(rows) > 1:
                logger.warning(
                    f"[WhatsApp] {len(rows)} active configurations share this webhook "
                    f"verify token (ids={[r.id for r in rows]}); refusing to verify "
                    "against an ambiguous one."
                )
                return None
            return rows[0] if rows else None

    async def set_telephony_configuration_inactive(
        self, config_id: int, organization_id: int, reason: str
    ) -> bool:
        """Deactivate a config, recording when and why."""
        async with self.async_session() as session:
            result = await session.execute(
                update(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
                .values(
                    inactive=True,
                    inactive_since=datetime.now(UTC),
                    inactive_reason=reason[:255],
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_telephony_configuration_active(
        self, config_id: int, organization_id: int
    ) -> bool:
        """Clear the inactive flag and the recorded deactivation details."""
        async with self.async_session() as session:
            result = await session.execute(
                update(TelephonyConfigurationModel)
                .where(
                    TelephonyConfigurationModel.id == config_id,
                    TelephonyConfigurationModel.organization_id == organization_id,
                )
                .values(
                    inactive=False,
                    inactive_since=None,
                    inactive_reason=None,
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def create_telephony_configuration(
        self,
        organization_id: int,
        name: str,
        provider: str,
        credentials: Dict[str, Any],
        is_default_outbound: bool = False,
    ) -> TelephonyConfigurationModel:
        """Create a new config row. Duplicate-account guarding is the caller's
        responsibility; this method does not enforce it.

        Which configuration is the default outbound is the customer's choice,
        so this stores exactly what the caller passed. The only write beyond
        the new row is demoting the previous default when this one claims it —
        part of honouring ``is_default_outbound=True``, since two defaults in
        one organization would make ``get_default_telephony_configuration``
        return an arbitrary row.
        """
        async with self.async_session() as session:
            if is_default_outbound:
                await self._clear_default_outbound(session, organization_id)

            row = TelephonyConfigurationModel(
                organization_id=organization_id,
                name=name,
                provider=provider,
                credentials=credentials,
                is_default_outbound=is_default_outbound,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TelephonyConfigurationConflictError(str(e)) from e
            await session.refresh(row)
            return row

    async def update_telephony_configuration(
        self,
        config_id: int,
        organization_id: int,
        name: Optional[str] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ) -> Optional[TelephonyConfigurationModel]:
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None

            if name is not None:
                row.name = name
            if credentials is not None:
                row.credentials = credentials

            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TelephonyConfigurationConflictError(str(e)) from e
            await session.refresh(row)
            return row

    async def set_default_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> Optional[TelephonyConfigurationModel]:
        """Mark this config as the org's default outbound, clearing any other default."""
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return None
            await self._clear_default_outbound(session, organization_id)
            row.is_default_outbound = True
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_telephony_configuration(
        self, config_id: int, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            row = await session.get(TelephonyConfigurationModel, config_id)
            if not row or row.organization_id != organization_id:
                return False

            campaign_ref = await session.execute(
                select(CampaignModel.id)
                .where(CampaignModel.telephony_configuration_id == config_id)
                .limit(1)
            )
            if campaign_ref.first():
                raise TelephonyConfigurationInUseError(
                    f"Telephony configuration {config_id} is referenced by one or "
                    f"more campaigns and cannot be deleted."
                )

            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def _clear_default_outbound(session, organization_id: int) -> None:
        await session.execute(
            update(TelephonyConfigurationModel)
            .where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.is_default_outbound.is_(True),
            )
            .values(is_default_outbound=False)
        )

    async def get_whatsapp_call_permission(
        self, telephony_configuration_id: int, recipient_phone_number: str
    ) -> Optional[WhatsAppCallPermissionModel]:
        """Fetch WhatsApp call permission record by config ID and recipient number (with format tolerance)."""
        candidates = _get_recipient_number_candidates(recipient_phone_number)
        async with self.async_session() as session:
            return await _select_permission_row(
                session,
                select(WhatsAppCallPermissionModel).where(
                    WhatsAppCallPermissionModel.telephony_configuration_id
                    == telephony_configuration_id,
                    WhatsAppCallPermissionModel.recipient_phone_number.in_(candidates),
                ),
                recipient_phone_number,
            )

    async def get_whatsapp_call_permission_by_phone_id(
        self, phone_number_id: str, recipient_phone_number: str
    ) -> Optional[WhatsAppCallPermissionModel]:
        """Fetch WhatsApp call permission record by Meta phone_number_id and recipient number (with format tolerance)."""
        async with self.async_session() as session:
            return await self._select_permission_row_for_phone_number_id(
                session,
                phone_number_id,
                recipient_phone_number,
                ambiguity_action="Skipping the permission lookup.",
            )

    @staticmethod
    async def _select_permission_row_for_phone_number_id(
        session,
        phone_number_id: str,
        recipient_phone_number: str,
        ambiguity_action: str,
    ) -> Optional[WhatsAppCallPermissionModel]:
        """Find a recipient's permission row from a Meta phone_number_id.

        Rows are written with the phone_number_id held in the configuration's
        credentials, which for a WABA-level setup is the business account id,
        while Meta's webhooks carry the id of the individual number underneath
        it. Requiring the stored id to equal the incoming one therefore matches
        nothing for exactly those configurations, and the update is dropped
        without a trace. The id is resolved to its configuration first and the
        row looked up by configuration and recipient, which is the pair the
        unique constraint is built on; an unresolvable id falls back to the
        literal match so nothing that worked before stops working.

        Returns None for an id that resolves to more than one active
        configuration rather than touching an arbitrary tenant's row.
        """
        candidates = _get_recipient_number_candidates(recipient_phone_number)
        configs = await _resolve_whatsapp_configs_by_phone_number_id(
            session, phone_number_id
        )
        if len(configs) > 1:
            _log_ambiguous_phone_number_id(phone_number_id, configs, ambiguity_action)
            return None

        config = next(iter(configs.values()), None)
        if config is not None:
            key_clause = (
                WhatsAppCallPermissionModel.telephony_configuration_id == config.id
            )
        else:
            key_clause = WhatsAppCallPermissionModel.phone_number_id == phone_number_id

        return await _select_permission_row(
            session,
            select(WhatsAppCallPermissionModel).where(
                key_clause,
                WhatsAppCallPermissionModel.recipient_phone_number.in_(candidates),
            ),
            recipient_phone_number,
        )

    async def upsert_whatsapp_call_permission(
        self,
        organization_id: int,
        telephony_configuration_id: int,
        phone_number_id: str,
        recipient_phone_number: str,
        status: str,
        permission_type: Optional[str] = UNSET,
        meta_message_id: Optional[str] = UNSET,
        expires_at: Optional[datetime] = UNSET,
        granted_at: Optional[datetime] = UNSET,
    ) -> WhatsAppCallPermissionModel:
        """Create or update a WhatsApp call permission record, handling concurrency races.

        ``permission_type``, ``meta_message_id``, ``expires_at`` and
        ``granted_at`` distinguish "not supplied" from an explicit ``None``:
        omitting one keeps the stored value, passing ``None`` clears it. When
        Meta reports a denial or a revocation, or a grant that carries no
        expiry, the caller passes ``None`` and means it — leaving the previous
        grant's future ``expires_at`` in place would advertise a revoked
        permission as usable to every reader that trusts the stored record
        while Meta is unreachable.
        """
        candidates = _get_recipient_number_candidates(recipient_phone_number)
        canonical_recipient = _canonical_recipient_number(recipient_phone_number)
        async with self.async_session() as session:
            try:
                row = await _select_permission_row(
                    session,
                    select(WhatsAppCallPermissionModel).where(
                        WhatsAppCallPermissionModel.telephony_configuration_id
                        == telephony_configuration_id,
                        WhatsAppCallPermissionModel.recipient_phone_number.in_(
                            candidates
                        ),
                    ),
                    recipient_phone_number,
                )
                now = datetime.now(UTC)
                if row is None:
                    row = WhatsAppCallPermissionModel(
                        organization_id=organization_id,
                        telephony_configuration_id=telephony_configuration_id,
                        phone_number_id=phone_number_id,
                        recipient_phone_number=canonical_recipient,
                        status=status,
                        permission_type=_supplied_or_none(permission_type),
                        meta_message_id=_supplied_or_none(meta_message_id),
                        requested_at=now,
                        granted_at=_supplied_or_none(granted_at),
                        expires_at=_supplied_or_none(expires_at),
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    _apply_permission_updates(
                        row,
                        status=status,
                        now=now,
                        phone_number_id=phone_number_id,
                        permission_type=permission_type,
                        meta_message_id=meta_message_id,
                        expires_at=expires_at,
                        granted_at=granted_at,
                    )

                await session.commit()
                await session.refresh(row)
                return row
            except IntegrityError:
                await session.rollback()
                # Concurrency race: another transaction inserted for this recipient
                logger.info(
                    f"[WhatsApp Permission] Concurrency race detected for recipient {recipient_phone_number} "
                    f"in configuration {telephony_configuration_id}; retrying as update"
                )
                row = await _select_permission_row(
                    session,
                    select(WhatsAppCallPermissionModel).where(
                        WhatsAppCallPermissionModel.telephony_configuration_id
                        == telephony_configuration_id,
                        WhatsAppCallPermissionModel.recipient_phone_number.in_(
                            candidates
                        ),
                    ),
                    recipient_phone_number,
                )
                if row is not None:
                    _apply_permission_updates(
                        row,
                        status=status,
                        now=datetime.now(UTC),
                        phone_number_id=phone_number_id,
                        permission_type=permission_type,
                        meta_message_id=meta_message_id,
                        expires_at=expires_at,
                        granted_at=granted_at,
                    )
                    await session.commit()
                    await session.refresh(row)
                    return row
                raise

    async def update_whatsapp_call_permission_status_by_wa_id(
        self,
        phone_number_id: str,
        recipient_phone_number: str,
        status: str,
        permission_type: Optional[str] = UNSET,
        expires_at: Optional[datetime] = UNSET,
        granted_at: Optional[datetime] = UNSET,
    ) -> Optional[WhatsAppCallPermissionModel]:
        """Update permission status by Meta phone_number_id and recipient number (e.g. from webhooks).

        The phone_number_id is resolved to its configuration before the row is
        located, so a WABA-level configuration whose rows carry the business
        account id still receives updates that arrive stamped with one of its
        numbers. As in the upsert, an omitted optional field keeps its stored
        value while an explicit ``None`` clears it.
        """
        async with self.async_session() as session:
            row = await self._select_permission_row_for_phone_number_id(
                session,
                phone_number_id,
                recipient_phone_number,
                ambiguity_action="Skipping the permission update.",
            )
            if not row:
                return None

            _apply_permission_updates(
                row,
                status=status,
                now=datetime.now(UTC),
                permission_type=permission_type,
                expires_at=expires_at,
                granted_at=granted_at,
            )

            await session.commit()
            await session.refresh(row)
            return row

    async def update_whatsapp_call_permission_status_by_message_id(
        self,
        meta_message_id: str,
        status: str,
        permission_type: Optional[str] = UNSET,
        expires_at: Optional[datetime] = UNSET,
        granted_at: Optional[datetime] = UNSET,
    ) -> Optional[WhatsAppCallPermissionModel]:
        """Update permission status by Meta message ID (e.g. from messages status or reply webhook).

        An omitted optional field keeps its stored value; an explicit ``None``
        clears it, so a decline reply drops the previous grant's expiry.

        ``meta_message_id`` is backed by a deliberately non-unique index (see
        ``ix_whatsapp_perm_meta_message_id``), so more than one row can share a
        wamid; the lookup goes through ``_select_permission_row`` so it always
        resolves to the same row instead of an arbitrary one, which would
        otherwise grant or deny an unrelated recipient.
        """
        async with self.async_session() as session:
            row = await _select_permission_row(
                session,
                select(WhatsAppCallPermissionModel).where(
                    WhatsAppCallPermissionModel.meta_message_id == meta_message_id,
                ),
                meta_message_id,
            )
            if not row:
                return None

            _apply_permission_updates(
                row,
                status=status,
                now=datetime.now(UTC),
                permission_type=permission_type,
                expires_at=expires_at,
                granted_at=granted_at,
            )
            await session.commit()
            await session.refresh(row)
            return row
