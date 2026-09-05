"""Shared pre-flight checks for every outbound telephony call path."""

from typing import Any

from api.db import db_client as default_db_client
from api.services.telephony import registry
from api.services.telephony.factory import get_setup_checklist


class OutboundReadinessError(ValueError):
    """Base error for a configuration that cannot currently place calls."""


class OutboundConfigurationNotFoundError(OutboundReadinessError):
    """Raised when no requested or usable outbound configuration is available."""


class OutboundSetupIncompleteError(OutboundReadinessError):
    """Raised when a provider's blocking setup steps are incomplete."""

    def __init__(
        self,
        telephony_configuration_id: int,
        configuration_name: str,
        blocked_reason: str,
    ) -> None:
        self.telephony_configuration_id = telephony_configuration_id
        self.configuration_name = configuration_name
        self.blocked_reason = blocked_reason
        super().__init__(
            f"'{configuration_name}' is not ready for outbound calls. {blocked_reason}"
        )


async def _ensure_row_outbound_setup_ready(row: Any, *, db: Any) -> int:
    numbers = await db.list_phone_numbers_for_config(row.id)
    active_numbers = [number for number in numbers if number.is_active]
    spec = registry.get_optional(row.provider)
    if spec is None:
        raise OutboundSetupIncompleteError(
            row.id,
            row.name,
            f"Provider '{row.provider}' is unavailable.",
        )
    trunks = await db.list_trunks_for_config(row.id) if spec.supports_trunks else []
    checklist = get_setup_checklist(
        row.provider,
        row.credentials or {},
        active_phone_number_count=len(active_numbers),
        inbound_routed_phone_number_count=len(
            [number for number in active_numbers if number.inbound_workflow_id]
        ),
        enabled_trunk_count=len([trunk for trunk in trunks if trunk.enabled]),
        unassigned_active_phone_number_count=len(
            [number for number in active_numbers if number.telephony_trunk_id is None]
        ),
    )
    if checklist is not None and not checklist.ready_for_outbound:
        raise OutboundSetupIncompleteError(
            row.id,
            row.name,
            checklist.outbound_blocked_reason
            or "Complete the required telephony setup before placing a call.",
        )

    return row.id


async def ensure_outbound_setup_ready(
    telephony_configuration_id: int,
    organization_id: int,
    *,
    db: Any = default_db_client,
) -> int:
    """Validate the exact, organization-scoped configuration selected by a caller."""
    if not organization_id:
        raise OutboundConfigurationNotFoundError("organization_id is required")

    row = await db.get_telephony_configuration_for_org(
        telephony_configuration_id,
        organization_id,
        active_only=True,
    )
    if row is None:
        raise OutboundConfigurationNotFoundError(
            f"Telephony configuration {telephony_configuration_id} not found "
            f"for organization {organization_id}"
        )
    return await _ensure_row_outbound_setup_ready(row, db=db)


async def resolve_outbound_configuration_id(
    telephony_configuration_id: int | None,
    organization_id: int,
    *,
    db: Any = default_db_client,
) -> int:
    """Resolve an explicit selection or choose the first ready active config.

    An explicit id is authoritative and never falls through to another config.
    Without one, DB ordering considers the marked default first, followed by
    the organization's remaining active configurations in creation order.
    """
    if telephony_configuration_id is not None:
        return await ensure_outbound_setup_ready(
            telephony_configuration_id,
            organization_id,
            db=db,
        )
    if not organization_id:
        raise OutboundConfigurationNotFoundError("organization_id is required")

    # TODO(multi-config): remove this compatibility fallback once all outbound
    # API contracts require callers to pin a telephony configuration id.
    candidates = await db.list_outbound_telephony_configuration_candidates(
        organization_id
    )
    if not candidates:
        raise OutboundConfigurationNotFoundError(
            f"No active telephony configuration found for organization "
            f"{organization_id}"
        )

    first_blocked: OutboundSetupIncompleteError | None = None
    for row in candidates:
        try:
            return await _ensure_row_outbound_setup_ready(row, db=db)
        except OutboundSetupIncompleteError as exc:
            if first_blocked is None:
                first_blocked = exc

    # ``candidates`` is non-empty, so every row produced a blocking reason.
    assert first_blocked is not None
    raise first_blocked


__all__ = [
    "OutboundConfigurationNotFoundError",
    "OutboundReadinessError",
    "OutboundSetupIncompleteError",
    "ensure_outbound_setup_ready",
    "resolve_outbound_configuration_id",
]
