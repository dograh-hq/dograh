"""Outbound readiness for a Cloudonix configuration.

The managed and customer-owned shapes deliberately block on different things,
and the whole point of the hook is that the UI can trust ``ready_for_outbound``
instead of discovering the truth from a failed call.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.telephony.factory import get_setup_checklist
from api.services.telephony.outbound_readiness import (
    OutboundSetupIncompleteError,
    ensure_outbound_setup_ready,
    resolve_outbound_configuration_id,
)
from api.services.telephony.providers.cloudonix.config import MANAGED_BY
from api.services.telephony.registry import ProviderSetupChecklist

MANAGED_CREDENTIALS = {
    "bearer_token": "domain-bearer",
    "domain_id": "oss-dograh-1111.cloudonix.net",
    "managed_by": MANAGED_BY,
}
SELF_SERVE_CREDENTIALS = {
    "bearer_token": "own-bearer",
    "domain_id": "acme.cloudonix.net",
}


def _checklist(
    credentials, *, numbers=0, inbound=0, trunks=0, unassigned=0
) -> ProviderSetupChecklist:
    checklist = get_setup_checklist(
        "cloudonix",
        credentials,
        active_phone_number_count=numbers,
        inbound_routed_phone_number_count=inbound,
        enabled_trunk_count=trunks,
        unassigned_active_phone_number_count=unassigned,
    )
    assert checklist is not None, "cloudonix must register a checklist resolver"
    return checklist


def _step(checklist, key):
    return next(step for step in checklist.steps if step.key == key)


def _keys(checklist):
    return [step.key for step in checklist.steps]


def test_freshly_provisioned_managed_configuration_is_not_ready():
    checklist = _checklist(MANAGED_CREDENTIALS)

    assert checklist.ready_for_outbound is False
    # The trunk is the first thing missing, so it is the reason surfaced.
    assert "trunk" in checklist.outbound_blocked_reason
    assert _step(checklist, "sip_domain").complete is True
    assert _step(checklist, "outbound_trunk").complete is False


def test_managed_configuration_with_a_trunk_still_needs_a_caller_id():
    checklist = _checklist(MANAGED_CREDENTIALS, trunks=1)

    assert checklist.ready_for_outbound is False
    assert "caller ID" in checklist.outbound_blocked_reason


def test_managed_configuration_is_ready_once_trunk_and_number_exist():
    checklist = _checklist(MANAGED_CREDENTIALS, numbers=1, trunks=1)

    assert checklist.ready_for_outbound is True
    assert checklist.outbound_blocked_reason is None
    # Inbound routing is still outstanding, but must not block dialling out.
    assert _step(checklist, "inbound_routing").complete is False
    assert _step(checklist, "inbound_routing").blocks_outbound is False


def test_a_switched_off_trunk_leaves_the_managed_domain_unready():
    """Incomplete trunks can't reach the checklist any more — the settings
    schema rejects a missing region or SIP domain on write — so the only way
    to have a trunk that doesn't count is to disable it."""
    checklist = _checklist(MANAGED_CREDENTIALS, numbers=1, trunks=0)

    assert checklist.ready_for_outbound is False
    assert _step(checklist, "outbound_trunk").complete is False


def test_trunk_assignment_is_only_asked_for_once_there_are_several_trunks():
    """Below two trunks the step is absent, not ticked.

    A ticked step renders as "you did this" next to a description asserting a
    second trunk — on a configuration that has none, both halves are false.
    """
    no_trunks = _checklist(MANAGED_CREDENTIALS)
    assert "trunk_assignment" not in _keys(no_trunks)

    one_trunk = _checklist(MANAGED_CREDENTIALS, numbers=2, trunks=1, unassigned=2)
    # A single trunk is unambiguous: the call path falls back to it.
    assert "trunk_assignment" not in _keys(one_trunk)
    assert one_trunk.ready_for_outbound is True

    several = _checklist(MANAGED_CREDENTIALS, numbers=2, trunks=2, unassigned=1)
    assert _step(several, "trunk_assignment").complete is False
    # Unassigned numbers still dial — they just go out unpinned — so this
    # nudges rather than blocks.
    assert _step(several, "trunk_assignment").blocks_outbound is False
    assert several.ready_for_outbound is True

    assigned = _checklist(MANAGED_CREDENTIALS, numbers=2, trunks=2, unassigned=0)
    assert _step(assigned, "trunk_assignment").complete is True


def test_customer_owned_domain_only_blocks_on_the_caller_id():
    """Their own domain may route through trunks configured in the Cockpit."""
    blocked = _checklist(SELF_SERVE_CREDENTIALS)
    assert blocked.ready_for_outbound is False
    assert "caller ID" in blocked.outbound_blocked_reason

    ready = _checklist(SELF_SERVE_CREDENTIALS, numbers=1)
    assert ready.ready_for_outbound is True
    assert _step(ready, "outbound_trunk").blocks_outbound is False


@pytest.mark.asyncio
async def test_outbound_guard_rejects_incomplete_configuration():
    row = SimpleNamespace(
        id=12,
        name="Dograh Cloudonix SIP",
        provider="cloudonix",
        credentials=MANAGED_CREDENTIALS,
    )
    db_client = AsyncMock()
    db_client.get_telephony_configuration_for_org = AsyncMock(return_value=row)
    db_client.list_phone_numbers_for_config = AsyncMock(return_value=[])
    db_client.list_trunks_for_config = AsyncMock(return_value=[])

    with pytest.raises(OutboundSetupIncompleteError) as excinfo:
        await ensure_outbound_setup_ready(12, 7, db=db_client)

    assert "Dograh Cloudonix SIP" in str(excinfo.value)


@pytest.mark.asyncio
async def test_outbound_guard_allows_a_ready_configuration():
    row = SimpleNamespace(
        id=12,
        name="Dograh Cloudonix SIP",
        provider="cloudonix",
        credentials=MANAGED_CREDENTIALS,
    )
    number = SimpleNamespace(
        is_active=True, inbound_workflow_id=None, telephony_trunk_id=1
    )
    trunk = SimpleNamespace(id=1, enabled=True)
    db_client = AsyncMock()
    db_client.get_telephony_configuration_for_org = AsyncMock(return_value=row)
    db_client.list_phone_numbers_for_config = AsyncMock(return_value=[number])
    db_client.list_trunks_for_config = AsyncMock(return_value=[trunk])

    assert await ensure_outbound_setup_ready(12, 7, db=db_client) == 12


@pytest.mark.asyncio
async def test_outbound_resolver_selects_a_ready_configuration_without_a_default():
    row = SimpleNamespace(
        id=12,
        name="Dograh Cloudonix SIP",
        provider="cloudonix",
        credentials=MANAGED_CREDENTIALS,
    )
    number = SimpleNamespace(
        is_active=True, inbound_workflow_id=None, telephony_trunk_id=1
    )
    trunk = SimpleNamespace(id=1, enabled=True)
    db_client = AsyncMock()
    db_client.list_outbound_telephony_configuration_candidates = AsyncMock(
        return_value=[row]
    )
    db_client.list_phone_numbers_for_config = AsyncMock(return_value=[number])
    db_client.list_trunks_for_config = AsyncMock(return_value=[trunk])

    assert await resolve_outbound_configuration_id(None, 7, db=db_client) == 12
    db_client.list_outbound_telephony_configuration_candidates.assert_awaited_once_with(
        7
    )


@pytest.mark.asyncio
async def test_outbound_resolver_skips_an_unready_candidate():
    blocked = SimpleNamespace(
        id=12,
        name="Fresh managed SIP",
        provider="cloudonix",
        credentials=MANAGED_CREDENTIALS,
    )
    ready = SimpleNamespace(
        id=13,
        name="Customer Cloudonix",
        provider="cloudonix",
        credentials=SELF_SERVE_CREDENTIALS,
    )
    number = SimpleNamespace(
        is_active=True, inbound_workflow_id=None, telephony_trunk_id=None
    )
    db_client = AsyncMock()
    db_client.list_outbound_telephony_configuration_candidates = AsyncMock(
        return_value=[blocked, ready]
    )
    db_client.list_phone_numbers_for_config = AsyncMock(
        side_effect=lambda config_id: [] if config_id == blocked.id else [number]
    )
    db_client.list_trunks_for_config = AsyncMock(return_value=[])

    assert await resolve_outbound_configuration_id(None, 7, db=db_client) == ready.id
