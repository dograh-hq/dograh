"""Database access for telephony phone numbers.

Phone numbers are first-class entities (PSTN, SIP URI, or SIP extension)
owned by a telephony configuration. They power both outbound caller-ID
selection and inbound call routing.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    WorkflowModel,
)
from api.utils.telephony_address import normalize_telephony_address


class TelephonyPhoneNumberConflictError(Exception):
    """Raised when a phone number violates a DB constraint."""


class TelephonyPhoneNumberClient(BaseDBClient):
    async def list_phone_numbers_for_config(
        self, telephony_configuration_id: int
    ) -> List[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel)
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return list(result.scalars().all())

    async def list_phone_numbers_with_workflow_name_for_config(
        self, telephony_configuration_id: int
    ) -> List[Tuple[TelephonyPhoneNumberModel, Optional[str]]]:
        """Same as :meth:`list_phone_numbers_for_config` but also returns the
        inbound workflow's display name (or None) for each row, fetched via a
        single LEFT JOIN so we don't load entire workflow rows."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel, WorkflowModel.name)
                .join(
                    WorkflowModel,
                    WorkflowModel.id == TelephonyPhoneNumberModel.inbound_workflow_id,
                    isouter=True,
                )
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return [(row, name) for row, name in result.all()]

    async def list_active_normalized_addresses_for_config(
        self, telephony_configuration_id: int
    ) -> List[str]:
        """Active phone numbers as canonical address strings (E.164 for PSTN,
        normalized SIP otherwise) — the shape providers want in their
        ``from_numbers`` list for caller-ID and rate-limit pool keys."""
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel.address_normalized)
                .where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                )
                .order_by(TelephonyPhoneNumberModel.created_at)
            )
            return [row[0] for row in result.all()]

    async def get_phone_number(
        self, phone_number_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            return await session.get(TelephonyPhoneNumberModel, phone_number_id)

    async def get_phone_number_for_config(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.id == phone_number_id,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                )
            )
            return result.scalars().first()

    async def find_active_phone_number_for_inbound(
        self,
        organization_id: int,
        address: str,
        provider: str,
        country_hint: Optional[str] = None,
    ) -> Optional[TelephonyPhoneNumberModel]:
        """Inbound routing primary lookup for an active number and config."""
        normalized = normalize_telephony_address(address, country_hint=country_hint)

        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel)
                .join(
                    TelephonyConfigurationModel,
                    TelephonyConfigurationModel.id
                    == TelephonyPhoneNumberModel.telephony_configuration_id,
                )
                .where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.inactive.is_(False),
                )
            )
            return result.scalars().first()

    async def find_inbound_route_by_account(
        self,
        provider: str,
        account_id_field: str,
        account_id: str,
        to_number: str,
        country_hint: Optional[str] = None,
        organization_id: Optional[int] = None,
    ) -> Optional[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Combined primary-path lookup for inbound dispatch.

        One SQL roundtrip that joins ``telephony_configurations`` and
        ``telephony_phone_numbers`` and matches all of:
        provider, ``credentials[account_id_field] == account_id``,
        ``phone.address_normalized == canonical(to_number)``, and
        ``phone.is_active``, and a non-parked configuration. Replaces the
        previous pattern of resolving the config and the phone number in two
        separate queries with a Python-side loop over candidate configs.

        Returns ``(config, phone_number)`` or None when the primary path
        misses (e.g. legacy non-E.164 stored addresses); the caller should
        fall back to the fuzzy ``numbers_match`` path in that case.
        """
        if not (provider and account_id_field and account_id and to_number):
            return None

        normalized = normalize_telephony_address(to_number, country_hint=country_hint)

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.credentials.op("->>")(account_id_field)
                    == account_id,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    TelephonyConfigurationModel.inactive.is_(False),
                )
            )
            if organization_id is not None:
                stmt = stmt.where(
                    TelephonyConfigurationModel.organization_id == organization_id
                )
            result = await session.execute(stmt)
            rows = result.all()

            if not rows:
                logger.info(
                    f"Inbound route lookup miss — provider={provider} "
                    f"{account_id_field}={account_id!r} "
                    f"to={to_number!r} canonical={normalized.canonical!r} "
                    f"org_scope={organization_id}"
                )
                return None

            # The (provider, account_id, address_normalized) tuple is meant
            # to be globally unique; the write paths that keep it so live in
            # ``api.services.telephony.inbound_routing``. When it isn't, the
            # call silently lands in whichever org Postgres returned first, so
            # name every candidate rather than only the winner.
            if len(rows) > 1:
                candidates = ", ".join(
                    f"config={cfg.id}/org={cfg.organization_id}/name={cfg.name!r}"
                    f"/phone={phone.id}"
                    for cfg, phone in rows
                )
                logger.error(
                    f"Ambiguous inbound route — provider={provider} "
                    f"{account_id_field}={account_id!r} "
                    f"canonical={normalized.canonical!r} matched {len(rows)} "
                    f"rows, using the first: {candidates}"
                )

            config, phone_number = rows[0][0], rows[0][1]
            logger.info(
                f"Inbound route resolved — provider={provider} "
                f"{account_id_field}={account_id!r} "
                f"canonical={normalized.canonical!r} -> config={config.id} "
                f"org={config.organization_id} name={config.name!r} "
                f"config_{account_id_field}="
                f"{(config.credentials or {}).get(account_id_field)!r} "
                f"phone={phone_number.id} address={phone_number.address!r} "
                f"inbound_workflow_id={phone_number.inbound_workflow_id}"
            )
            return config, phone_number

    async def find_inbound_route_by_called_number(
        self,
        provider: str,
        to_number: str,
        country_hint: Optional[str] = None,
    ) -> Optional[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Inbound route lookup when the webhook omits account_id.

        Some providers (Exotel Voicebot Applet dynamic URL) POST/GET inbound
        webhooks without an account identifier. In that case we match on
        ``(provider, canonical called number)`` across all orgs. Returns a
        route only when exactly one active, non-parked config owns that
        number; returns None on zero matches or when multiple configs would
        race for the same call.
        """
        if not (provider and to_number):
            return None

        normalized = normalize_telephony_address(to_number, country_hint=country_hint)

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyPhoneNumberModel.address_normalized
                    == normalized.canonical,
                    TelephonyPhoneNumberModel.is_active.is_(True),
                    TelephonyConfigurationModel.inactive.is_(False),
                )
                .limit(2)
            )
            result = await session.execute(stmt)
            rows = result.all()
            if len(rows) == 1:
                return rows[0][0], rows[0][1]
            return None

    async def find_inbound_routing_conflicts(
        self,
        provider: str,
        account_id_field: str,
        account_id: str,
        addresses_normalized: Sequence[str],
        exclude_configuration_id: Optional[int] = None,
    ) -> List[Tuple[TelephonyConfigurationModel, TelephonyPhoneNumberModel]]:
        """Rows that already hold one of these inbound routing keys.

        Inbound dispatch keys on (provider, credentials[account_id_field],
        address_normalized) — see ``find_inbound_route_by_account``. That tuple
        must be globally unique or two orgs race for the same call. The rule and
        the decision of when to apply it live in
        ``api.services.telephony.inbound_routing``; this is only its query.

        Returns every conflicting (config, phone_number) pair, possibly owned by
        other organizations. ``exclude_configuration_id`` drops the configuration
        being updated so it cannot conflict with itself. Addresses are matched as
        already-canonical ``address_normalized`` values — callers normalize.
        """
        if not (provider and account_id_field and account_id and addresses_normalized):
            return []

        async with self.async_session() as session:
            stmt = (
                select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
                .join(
                    TelephonyPhoneNumberModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    TelephonyConfigurationModel.provider == provider,
                    TelephonyConfigurationModel.credentials.op("->>")(account_id_field)
                    == account_id,
                    TelephonyPhoneNumberModel.address_normalized.in_(
                        list(addresses_normalized)
                    ),
                )
            )
            if exclude_configuration_id is not None:
                stmt = stmt.where(
                    TelephonyConfigurationModel.id != exclude_configuration_id
                )
            result = await session.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]

    async def create_phone_number(
        self,
        organization_id: int,
        telephony_configuration_id: int,
        address: str,
        country_code: Optional[str] = None,
        label: Optional[str] = None,
        inbound_workflow_id: Optional[int] = None,
        telephony_trunk_id: Optional[int] = None,
        is_active: bool = True,
        is_default_caller_id: bool = False,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> TelephonyPhoneNumberModel:
        normalized = normalize_telephony_address(address, country_hint=country_code)

        async with self.async_session() as session:
            if is_default_caller_id:
                await self._clear_default_caller_id(session, telephony_configuration_id)

            row = TelephonyPhoneNumberModel(
                organization_id=organization_id,
                telephony_configuration_id=telephony_configuration_id,
                address=address,
                address_normalized=normalized.canonical,
                address_type=normalized.address_type,
                country_code=country_code or normalized.country_code,
                label=label,
                inbound_workflow_id=inbound_workflow_id,
                telephony_trunk_id=telephony_trunk_id,
                is_active=is_active,
                is_default_caller_id=is_default_caller_id,
                extra_metadata=extra_metadata or {},
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise TelephonyPhoneNumberConflictError(str(e)) from e
            await session.refresh(row)
            return row

    async def update_phone_number(
        self,
        phone_number_id: int,
        telephony_configuration_id: int,
        label: Optional[str] = None,
        inbound_workflow_id: Optional[int] = None,
        telephony_trunk_id: Optional[int] = None,
        is_active: Optional[bool] = None,
        country_code: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        clear_inbound_workflow: bool = False,
        clear_trunk: bool = False,
    ) -> Optional[TelephonyPhoneNumberModel]:
        """Partial update. ``address`` is intentionally immutable — create a new
        row instead. Set ``clear_inbound_workflow``/``clear_trunk`` to null out
        the respective FK."""
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return None

            if label is not None:
                row.label = label
            if inbound_workflow_id is not None:
                row.inbound_workflow_id = inbound_workflow_id
            elif clear_inbound_workflow:
                row.inbound_workflow_id = None
            if telephony_trunk_id is not None:
                row.telephony_trunk_id = telephony_trunk_id
            elif clear_trunk:
                row.telephony_trunk_id = None
            if is_active is not None:
                row.is_active = is_active
            if country_code is not None:
                row.country_code = country_code
            if extra_metadata is not None:
                row.extra_metadata = extra_metadata

            await session.commit()
            await session.refresh(row)
            return row

    async def set_default_caller_id(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return None
            await self._clear_default_caller_id(session, telephony_configuration_id)
            row.is_default_caller_id = True
            await session.commit()
            await session.refresh(row)
            return row

    async def get_default_caller_id(
        self, telephony_configuration_id: int
    ) -> Optional[TelephonyPhoneNumberModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == telephony_configuration_id,
                    TelephonyPhoneNumberModel.is_default_caller_id.is_(True),
                )
            )
            return result.scalars().first()

    async def delete_phone_number(
        self, phone_number_id: int, telephony_configuration_id: int
    ) -> bool:
        async with self.async_session() as session:
            row = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not row or row.telephony_configuration_id != telephony_configuration_id:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    async def _clear_default_caller_id(
        session, telephony_configuration_id: int
    ) -> None:
        await session.execute(
            update(TelephonyPhoneNumberModel)
            .where(
                TelephonyPhoneNumberModel.telephony_configuration_id
                == telephony_configuration_id,
                TelephonyPhoneNumberModel.is_default_caller_id.is_(True),
            )
            .values(is_default_caller_id=False)
        )

    async def list_platform_numbers(
        self, organization_id: int
    ) -> List[Dict[str, Any]]:
        """List platform inventory numbers visible to an organization.

        Shared trial numbers are visible to everyone.
        Dedicated numbers are visible if unassigned OR if assigned to this organization.
        """
        async with self.async_session() as session:
            stmt = (
                select(TelephonyPhoneNumberModel, TelephonyConfigurationModel)
                .join(
                    TelephonyConfigurationModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(
                    or_(
                        TelephonyPhoneNumberModel.is_platform_inventory == True,
                        TelephonyPhoneNumberModel.organization_id == organization_id,
                        TelephonyPhoneNumberModel.assigned_organization_id == organization_id,
                    ),
                    TelephonyPhoneNumberModel.is_active == True,
                )
                .order_by(
                    TelephonyPhoneNumberModel.pool_type.desc(),
                    TelephonyPhoneNumberModel.created_at,
                )
            )
            result = await session.execute(stmt)
            rows = result.all()

            output = []
            for num, config in rows:
                is_shared = num.pool_type == "shared_trial"
                is_assigned = (num.assigned_organization_id is not None) or (num.organization_id == organization_id)
                is_assigned_to_current = (num.assigned_organization_id == organization_id) or (num.organization_id == organization_id)

                # If dedicated and assigned to someone else, hide it
                if not is_shared and is_assigned and not is_assigned_to_current:
                    continue

                output.append(
                    {
                        "id": num.id,
                        "phone_number": num.address,
                        "carrier": config.provider,
                        "pool_type": num.pool_type,
                        "monthly_price_cents": num.monthly_price_cents,
                        "in_use": not is_shared and is_assigned,
                        "is_claimed_by_you": is_assigned_to_current,
                        "country_code": num.country_code,
                        "telephony_configuration_id": num.telephony_configuration_id,
                    }
                )
            return output

    async def list_all_platform_inventory(self) -> List[Dict[str, Any]]:
        """List all platform inventory numbers for superadmin view."""
        async with self.async_session() as session:
            stmt = (
                select(TelephonyPhoneNumberModel, TelephonyConfigurationModel)
                .join(
                    TelephonyConfigurationModel,
                    TelephonyPhoneNumberModel.telephony_configuration_id
                    == TelephonyConfigurationModel.id,
                )
                .where(TelephonyPhoneNumberModel.is_platform_inventory == True)
                .order_by(TelephonyPhoneNumberModel.created_at.desc())
            )
            result = await session.execute(stmt)
            rows = result.all()

            return [
                {
                    "id": num.id,
                    "phone_number": num.address,
                    "carrier": config.provider,
                    "configuration_id": config.id,
                    "configuration_name": config.name,
                    "pool_type": num.pool_type,
                    "monthly_price_cents": num.monthly_price_cents,
                    "assigned_organization_id": num.assigned_organization_id,
                    "is_active": num.is_active,
                    "created_at": num.created_at.isoformat() if num.created_at else None,
                }
                for num, config in rows
            ]

    async def claim_platform_number(
        self,
        phone_number_id: int,
        organization_id: int,
        set_as_default: bool = True,
    ) -> Dict[str, Any]:
        """Claim a platform number for an organization and provision a linked config."""
        async with self.async_session() as session:
            num = await session.get(TelephonyPhoneNumberModel, phone_number_id)
            if not num or not num.is_platform_inventory:
                raise ValueError("Platform number not found")

            if num.pool_type != "shared_trial" and num.assigned_organization_id is not None:
                if num.assigned_organization_id != organization_id:
                    raise ValueError("This number has already been claimed by another organization")

            # Mark inventory number assigned
            num.assigned_organization_id = organization_id

            # Also check if organization already has a linked platform config, or clone/bind one
            source_config = await session.get(
                TelephonyConfigurationModel, num.telephony_configuration_id
            )
            if not source_config:
                raise ValueError("Source telephony configuration not found")

            # If setting as default outbound, clear previous defaults for the org
            from sqlalchemy import update
            if set_as_default:
                await session.execute(
                    update(TelephonyConfigurationModel)
                    .where(TelephonyConfigurationModel.organization_id == organization_id)
                    .values(is_default_outbound=False)
                )

            # Find or create a matching telephony config inside user's org
            stmt = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.name == f"Platform - {source_config.name}",
            )
            existing_org_config = (await session.execute(stmt)).scalar_one_or_none()

            if not existing_org_config:
                existing_org_config = TelephonyConfigurationModel(
                    organization_id=organization_id,
                    name=f"Platform - {source_config.name}",
                    provider=source_config.provider,
                    credentials=source_config.credentials,
                    is_default_outbound=set_as_default,
                )
                session.add(existing_org_config)
                await session.flush()
            elif set_as_default:
                existing_org_config.is_default_outbound = True

            # If setting as default caller ID, clear previous default callers in this config
            if set_as_default:
                await session.execute(
                    update(TelephonyPhoneNumberModel)
                    .where(TelephonyPhoneNumberModel.telephony_configuration_id == existing_org_config.id)
                    .values(is_default_caller_id=False)
                )

            # Ensure phone number copy exists in organization's telephony config for campaigns
            stmt_num = select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.organization_id == organization_id,
                TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
            )
            org_num = (await session.execute(stmt_num)).scalar_one_or_none()
            if not org_num:
                org_num = TelephonyPhoneNumberModel(
                    organization_id=organization_id,
                    telephony_configuration_id=existing_org_config.id,
                    address=num.address,
                    address_normalized=num.address_normalized,
                    address_type=num.address_type,
                    country_code=num.country_code,
                    label=f"Claimed {num.address}",
                    is_active=True,
                    is_default_caller_id=set_as_default,
                    pool_type=num.pool_type,
                    is_platform_inventory=False,
                )
                session.add(org_num)
            else:
                org_num.telephony_configuration_id = existing_org_config.id
                org_num.is_active = True
                if set_as_default:
                    org_num.is_default_caller_id = True

            num_id = num.id
            num_address = str(num.address)
            num_pool_type = str(num.pool_type)
            carrier = str(source_config.provider) if source_config.provider else "carrier"
            assigned_config_id = int(existing_org_config.id)

            await session.commit()

            return {
                "id": num_id,
                "phone_number": num_address,
                "carrier": carrier,
                "pool_type": num_pool_type,
                "organization_id": organization_id,
                "telephony_configuration_id": assigned_config_id,
                "message": f"Successfully provisioned {num_address} into your workspace!",
            }

