"""Phone number synchronization service for telephony providers."""

from typing import Any

from loguru import logger

from api.db import db_client
from api.db.telephony_phone_number_client import TelephonyPhoneNumberConflictError
from api.schemas.telephony_phone_number import ProviderSyncStatus
from api.services.telephony.factory import get_telephony_provider_by_id
from api.services.telephony.inbound_routing import (
    InboundRoutingConflictError,
    assert_no_inbound_routing_conflict,
)
from api.utils.telephony_address import normalize_telephony_address


async def sync_available_phone_numbers_for_config(
    config_id: int, organization_id: int
) -> ProviderSyncStatus:
    """Import provider-owned outbound/inbound numbers into Dograh if the provider exposes them."""
    try:
        provider = await get_telephony_provider_by_id(config_id, organization_id)
    except Exception as e:
        logger.error(f"Failed to load telephony provider for config {config_id}: {e}")
        return ProviderSyncStatus(ok=False, message=f"Provider load failed: {e}")

    discover_records = getattr(provider, "get_available_phone_number_records", None)
    records: list[dict[str, Any]] = []

    if callable(discover_records):
        try:
            records = await discover_records()
        except Exception as e:
            logger.error(
                f"Failed to discover phone number records for config {config_id}: {e}"
            )
            return ProviderSyncStatus(ok=False, message=f"Provider sync failed: {e}")
    else:
        discover_numbers = getattr(provider, "get_available_phone_numbers", None)
        if not callable(discover_numbers):
            return ProviderSyncStatus(
                ok=True,
                message="Provider does not expose discoverable outbound numbers.",
            )

        try:
            available_numbers = await discover_numbers()
            records = [{"address": num} for num in (available_numbers or [])]
        except Exception as e:
            logger.error(
                f"Failed to discover phone numbers for config {config_id}: {e}"
            )
            return ProviderSyncStatus(ok=False, message=f"Provider sync failed: {e}")

    existing_rows = await db_client.list_phone_numbers_for_config(config_id)
    existing_map = {
        row.address_normalized: row for row in existing_rows if row.address_normalized
    }
    has_default_caller = any(
        getattr(row, "is_default_caller_id", False)
        for row in existing_rows
        if getattr(row, "is_active", True)
    )
    imported = 0
    skipped = 0
    deactivated = 0
    discovered: set[str] = set()

    config_row = await db_client.get_telephony_configuration(config_id)
    provider_name = config_row.provider if config_row else None
    provider_credentials = config_row.credentials if config_row else None

    for item in records:
        address = item.get("address")
        if not address:
            continue
        try:
            normalized = normalize_telephony_address(address).canonical
        except ValueError:
            skipped += 1
            logger.warning(
                f"Skipping unparseable phone number discovered for config {config_id}: "
                f"{address!r}"
            )
            continue

        discovered.add(normalized)
        extra_metadata = item.get("extra_metadata") or {}

        if normalized in existing_map:
            existing_row = existing_map[normalized]
            if existing_row:
                updates: dict[str, Any] = {}
                if not getattr(existing_row, "is_active", True):
                    updates["is_active"] = True
                if extra_metadata:
                    merged = {**(existing_row.extra_metadata or {}), **extra_metadata}
                    if merged != existing_row.extra_metadata:
                        updates["extra_metadata"] = merged
                if updates:
                    await db_client.update_phone_number(
                        phone_number_id=existing_row.id,
                        telephony_configuration_id=config_id,
                        **updates,
                    )
                    if updates.get("is_active"):
                        logger.info(
                            f"Reactivated returned phone number {normalized!r} for config {config_id}"
                        )
            continue

        try:
            if provider_name:
                await assert_no_inbound_routing_conflict(
                    provider=provider_name,
                    credentials=provider_credentials,
                    addresses=[normalized],
                    organization_id=organization_id,
                )
            await db_client.create_phone_number(
                organization_id=organization_id,
                telephony_configuration_id=config_id,
                address=address,
                is_active=True,
                is_default_caller_id=not has_default_caller and imported == 0,
                extra_metadata=extra_metadata,
            )
            imported += 1
            existing_map[normalized] = None
            if not has_default_caller:
                has_default_caller = True
        except TelephonyPhoneNumberConflictError:
            skipped += 1
            logger.info(
                f"Skipping already-existing phone number {address!r} while syncing "
                f"config {config_id}"
            )
        except InboundRoutingConflictError:
            skipped += 1
            logger.warning(
                f"Skipping conflicting phone number {address!r} while syncing config {config_id}: "
                "already registered on another inbound route"
            )

    # Deactivate rows that are no longer present in the provider result, but only if
    # the provider performed an account-wide inventory. Avoid deactivating valid numbers
    # if discovery only returned a partial result (e.g. single directly queried number).
    is_full_inventory = getattr(provider, "is_full_inventory", True)
    if is_full_inventory:
        for row in existing_rows:
            if getattr(row, "is_active", True) and getattr(row, "address_normalized", None) and row.address_normalized not in discovered:
                updates = {"is_active": False}
                if getattr(row, "is_default_caller_id", False):
                    updates["is_default_caller_id"] = False
                row_id = getattr(row, "id", None)
                if not row_id:
                    continue
                await db_client.update_phone_number(
                    phone_number_id=row_id,
                    telephony_configuration_id=config_id,
                    **updates,
                )
                deactivated += 1
                logger.info(
                    f"Deactivated stale phone number {row.address_normalized!r} for config {config_id}: "
                    "no longer present in provider result"
                )

    if imported or deactivated:
        parts = []
        if imported:
            parts.append(f"Imported {imported} phone number(s).")
        if deactivated:
            parts.append(f"Deactivated {deactivated} stale phone number(s).")
        message = " ".join(parts)
        if skipped:
            message += f" Skipped {skipped} duplicate or invalid number(s)."
        return ProviderSyncStatus(ok=True, message=message)

    if not records:
        return ProviderSyncStatus(
            ok=True,
            message="No discoverable phone numbers were returned by the provider.",
        )

    return ProviderSyncStatus(
        ok=True,
        message=f"No new phone numbers to import ({skipped} duplicate or invalid number(s) skipped)."
        if skipped
        else "No new phone numbers to import.",
    )
