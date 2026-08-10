"""Lease semantics for organization configuration keys used as locks.

These run against the real database on purpose: the whole point of the lease is
that Postgres — not application code — decides the single winner, so mocking the
session would assert nothing.
"""

from datetime import timedelta

import pytest

from api.db.organization_configuration_client import LEASE_COMPLETED, LEASE_PENDING

LEASE_KEY = "TEST_LEASE"

# The claim performs its own round trips after stamping `updated_at`, so a zero
# window is still strictly in the past by the time the takeover predicate runs.
ALREADY_STALE = timedelta(0)
NEVER_STALE = timedelta(hours=1)


async def _make_organization(db_session, suffix: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"lease-user-{suffix}")
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        org_provider_id=f"lease-org-{suffix}", user_id=user.id
    )
    return organization


@pytest.mark.asyncio
async def test_only_one_concurrent_claimant_wins(db_session):
    organization = await _make_organization(db_session, "one-winner")

    first = await db_session.claim_configuration_lease(
        organization.id, LEASE_KEY, NEVER_STALE
    )
    second = await db_session.claim_configuration_lease(
        organization.id, LEASE_KEY, NEVER_STALE
    )

    assert first is True
    assert second is False

    row = await db_session.get_configuration(organization.id, LEASE_KEY)
    assert row.value == {"status": LEASE_PENDING}


@pytest.mark.asyncio
async def test_lease_is_scoped_per_organization(db_session):
    first_org = await _make_organization(db_session, "scope-a")
    second_org = await _make_organization(db_session, "scope-b")

    assert (
        await db_session.claim_configuration_lease(first_org.id, LEASE_KEY, NEVER_STALE)
        is True
    )
    assert (
        await db_session.claim_configuration_lease(
            second_org.id, LEASE_KEY, NEVER_STALE
        )
        is True
    )


@pytest.mark.asyncio
async def test_stale_pending_lease_is_taken_over(db_session):
    """A holder that died mid-work must not block the organization forever."""
    organization = await _make_organization(db_session, "stale-takeover")

    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, NEVER_STALE
        )
        is True
    )
    # Still held, and not yet stale.
    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, NEVER_STALE
        )
        is False
    )
    # Past the staleness window, the next caller takes it over.
    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, ALREADY_STALE
        )
        is True
    )


@pytest.mark.asyncio
async def test_completed_lease_is_never_taken_over(db_session):
    """Terminal means terminal — the leased work must not run a second time."""
    organization = await _make_organization(db_session, "completed")

    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, NEVER_STALE
        )
        is True
    )
    await db_session.complete_configuration_lease(organization.id, LEASE_KEY)

    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, ALREADY_STALE
        )
        is False
    )

    row = await db_session.get_configuration(organization.id, LEASE_KEY)
    assert row.value == {"status": LEASE_COMPLETED}


@pytest.mark.asyncio
async def test_released_lease_is_reclaimable_immediately(db_session):
    """A failed holder releases, so the retry does not wait out the stale window."""
    organization = await _make_organization(db_session, "released")

    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, NEVER_STALE
        )
        is True
    )
    await db_session.release_configuration_lease(organization.id, LEASE_KEY)

    assert await db_session.get_configuration(organization.id, LEASE_KEY) is None
    assert (
        await db_session.claim_configuration_lease(
            organization.id, LEASE_KEY, NEVER_STALE
        )
        is True
    )


@pytest.mark.asyncio
async def test_release_does_not_drop_a_completed_lease(db_session):
    """Releasing after completion would re-open work that already succeeded."""
    organization = await _make_organization(db_session, "release-completed")

    await db_session.claim_configuration_lease(organization.id, LEASE_KEY, NEVER_STALE)
    await db_session.complete_configuration_lease(organization.id, LEASE_KEY)
    await db_session.release_configuration_lease(organization.id, LEASE_KEY)

    row = await db_session.get_configuration(organization.id, LEASE_KEY)
    assert row is not None
    assert row.value == {"status": LEASE_COMPLETED}
