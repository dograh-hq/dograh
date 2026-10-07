from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api.db.organization_configuration_client import LEASE_COMPLETED, LEASE_PENDING
from api.schemas.model_connections import ModelConfigurationSpec
from api.services import organization_bootstrap as bootstrap

ORG_ID = 42
CREATED_BY = "provider-user"
EXISTING_DEFAULT = "existing-configuration-uuid"
MINTED_KEY = "minted-svc-key"
LEASE_OWNER_TOKEN = "lease-owner-token"


@pytest.fixture(autouse=True)
def state(monkeypatch):
    """Bootstrap sentinel and catalog default; both absent by default.

    Autouse so no test hits the DB.
    """
    state = SimpleNamespace(sentinel=None, default=None)

    async def read(*_):
        values = {
            bootstrap._BOOTSTRAP_KEY: state.sentinel,
            bootstrap._CATALOG_DEFAULT_KEY: state.default,
        }
        return {key: value for key, value in values.items() if value is not None}

    monkeypatch.setattr(
        bootstrap.db_client, "get_configuration_values", AsyncMock(side_effect=read)
    )
    return state


@pytest.fixture(autouse=True)
def sip_present(monkeypatch):
    """Whether managed SIP already exists; False by default."""
    mock = AsyncMock(return_value=False)
    monkeypatch.setattr(bootstrap, "_has_managed_sip_connectivity", mock)
    return mock


@pytest.fixture(autouse=True)
def sip(monkeypatch):
    """Provisioning of managed SIP. Autouse so no test reaches a real provider."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(bootstrap, "provision_managed_sip_connectivity", mock)
    return mock


@pytest.fixture
def lease(monkeypatch):
    calls = SimpleNamespace(
        claim=AsyncMock(return_value=LEASE_OWNER_TOKEN),
        complete=AsyncMock(),
        release=AsyncMock(),
    )
    monkeypatch.setattr(bootstrap.db_client, "claim_configuration_lease", calls.claim)
    monkeypatch.setattr(
        bootstrap.db_client, "complete_configuration_lease", calls.complete
    )
    monkeypatch.setattr(
        bootstrap.db_client, "release_configuration_lease", calls.release
    )
    return calls


@pytest.fixture
def mps(monkeypatch):
    create_service_key = AsyncMock(return_value={"service_key": MINTED_KEY})
    monkeypatch.setattr(
        bootstrap.mps_service_key_client, "create_service_key", create_service_key
    )
    monkeypatch.setattr(bootstrap, "ensure_hosted_mps_billing_account_v2", AsyncMock())
    return create_service_key


@pytest.fixture
def catalog(monkeypatch):
    """The atomic connection + default configuration write."""
    mock = AsyncMock(return_value="new-configuration-uuid")
    monkeypatch.setattr(
        bootstrap.db_client, "bootstrap_default_model_configuration", mock
    )
    return mock


@pytest.mark.asyncio
async def test_completed_sentinel_short_circuits(state, lease, mps, catalog, sip):
    state.sentinel = {"status": LEASE_COMPLETED}

    assert await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    lease.claim.assert_not_awaited()
    mps.assert_not_awaited()
    sip.assert_not_awaited()
    bootstrap.db_client.get_configuration_values.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_sentinel_does_not_short_circuit(
    state, lease, mps, catalog, sip, sip_present
):
    """Only a terminal sentinel means done; a pending one is work in progress."""
    state.sentinel = {"status": LEASE_PENDING}
    state.default = EXISTING_DEFAULT

    await bootstrap.ensure_organization_bootstrapped(ORG_ID, created_by=CREATED_BY)

    lease.claim.assert_awaited_once()


@pytest.mark.asyncio
async def test_fully_provisioned_org_backfills_the_sentinel(
    state, lease, mps, catalog, sip, sip_present
):
    """Orgs provisioned before the sentinel existed must reach the fast path."""
    state.default = EXISTING_DEFAULT
    sip_present.return_value = True

    assert await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    lease.claim.assert_awaited_once()
    mps.assert_not_awaited()
    catalog.assert_not_awaited()
    sip.assert_not_awaited()
    lease.complete.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )


@pytest.mark.asyncio
async def test_existing_org_gets_owner_scoped_sip_without_minting_a_second_key(
    state, lease, mps, catalog, sip
):
    """The backfill case: a default configuration exists, SIP does not.

    Re-minting would strand the org's current key and issue a second billable
    one. SIP is independent and uses the bootstrap owner's identity. This holds
    whatever providers the default uses.
    """
    state.default = EXISTING_DEFAULT

    assert await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    mps.assert_not_awaited()
    catalog.assert_not_awaited()
    sip.assert_awaited_once_with(ORG_ID, created_by=CREATED_BY)
    lease.complete.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )


@pytest.mark.asyncio
async def test_new_org_mints_key_and_independently_provisions_sip(
    state, lease, mps, catalog, sip
):
    assert await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    mps.assert_awaited_once()
    catalog.assert_awaited_once()
    assert catalog.await_args.args == (ORG_ID,)
    kwargs = catalog.await_args.kwargs
    assert kwargs["provider"] == "dograh"
    assert kwargs["credentials"] == {"api_key": MINTED_KEY}
    connection_uuid = str(uuid4())
    spec = ModelConfigurationSpec.model_validate(
        kwargs["configuration_for"](connection_uuid)
    )
    assert spec.mode == "pipeline"
    assert {
        str(getattr(spec, role).provider_connection_uuid)
        for role in ("llm", "stt", "tts", "embeddings")
    } == {connection_uuid}
    sip.assert_awaited_once_with(ORG_ID, created_by=CREATED_BY)
    lease.complete.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )


@pytest.mark.asyncio
async def test_losing_the_lease_skips_provisioning(state, lease, mps, catalog, sip):
    """A concurrent request already holds it; minting again would duplicate keys."""
    lease.claim.return_value = None

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    mps.assert_not_awaited()
    catalog.assert_not_awaited()
    sip.assert_not_awaited()
    lease.complete.assert_not_awaited()
    lease.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_key_mint_failure_releases_the_lease(state, lease, mps, catalog, sip):
    """Nothing was persisted, so the next request should retry immediately."""
    mps.side_effect = RuntimeError("MPS down")

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    catalog.assert_not_awaited()
    lease.release.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )
    lease.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_service_key_is_treated_as_failure(state, lease, mps, catalog):
    """A 200 with no key must not mark the org bootstrapped and never retry."""
    mps.return_value = {}

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    catalog.assert_not_awaited()
    lease.release.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )
    lease.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_sip_failure_leaves_the_lease_pending_for_a_throttled_retry(
    state, lease, mps, catalog, sip
):
    """The config is persisted, so releasing would retry a failing provider on
    every request; the staleness window should pace it instead."""
    state.default = EXISTING_DEFAULT
    sip.return_value = False

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    lease.complete.assert_not_awaited()
    lease.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_org_keeps_its_configuration_when_sip_fails(
    state, lease, mps, catalog, sip
):
    sip.return_value = False

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    catalog.assert_awaited_once()
    lease.release.assert_not_awaited()


@pytest.mark.asyncio
async def test_billing_failure_does_not_discard_the_model_configuration(
    monkeypatch, state, lease, mps, catalog, sip
):
    monkeypatch.setattr(
        bootstrap,
        "ensure_hosted_mps_billing_account_v2",
        AsyncMock(side_effect=RuntimeError("billing down")),
    )

    assert await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    catalog.assert_awaited_once()
    lease.complete.assert_awaited_once_with(
        ORG_ID, bootstrap._BOOTSTRAP_KEY, LEASE_OWNER_TOKEN
    )


@pytest.mark.asyncio
async def test_unreadable_state_never_fails_authentication(
    monkeypatch, state, lease, mps, catalog, sip
):
    monkeypatch.setattr(
        bootstrap.db_client,
        "get_configuration_values",
        AsyncMock(side_effect=RuntimeError("db down")),
    )

    assert not await bootstrap.ensure_organization_bootstrapped(
        ORG_ID, created_by=CREATED_BY
    )

    lease.claim.assert_not_awaited()
    mps.assert_not_awaited()
