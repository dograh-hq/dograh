from uuid import uuid4

import pytest

from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)


@pytest.mark.asyncio
async def test_runtime_configuration_queries_exclude_inactive_rows(
    async_session, db_session
):
    organization = OrganizationModel(provider_id=f"inactive-routing-{uuid4()}")
    async_session.add(organization)
    await async_session.flush()

    inactive = TelephonyConfigurationModel(
        organization_id=organization.id,
        name="Parked ARI",
        provider="ari",
        credentials={},
        is_default_outbound=True,
        inactive=True,
    )
    active = TelephonyConfigurationModel(
        organization_id=organization.id,
        name="Active ARI",
        provider="ari",
        credentials={},
        is_default_outbound=False,
        inactive=False,
    )
    async_session.add_all([inactive, active])
    await async_session.flush()

    assert (
        await db_session.get_telephony_configuration_for_org(
            inactive.id, organization.id
        )
        is None
    )
    assert (
        await db_session.get_telephony_configuration_for_org(
            inactive.id, organization.id, active_only=False
        )
    ).id == inactive.id

    assert await db_session.get_default_telephony_configuration(organization.id) is None
    assert (
        await db_session.get_default_telephony_configuration(
            organization.id, active_only=False
        )
    ).id == inactive.id

    active_candidates = await db_session.list_telephony_configurations_by_provider(
        organization.id, "ari"
    )
    assert [row.id for row in active_candidates] == [active.id]

    all_candidates = await db_session.list_telephony_configurations_by_provider(
        organization.id, "ari", active_only=False
    )
    assert {row.id for row in all_candidates} == {inactive.id, active.id}


@pytest.mark.asyncio
async def test_inbound_route_queries_exclude_inactive_configuration(
    async_session, db_session
):
    organization = OrganizationModel(provider_id=f"inactive-inbound-{uuid4()}")
    async_session.add(organization)
    await async_session.flush()

    config = TelephonyConfigurationModel(
        organization_id=organization.id,
        name="Parked Twilio",
        provider="twilio",
        credentials={"account_sid": "AC-parked"},
        is_default_outbound=True,
        inactive=True,
    )
    async_session.add(config)
    await async_session.flush()

    phone_number = TelephonyPhoneNumberModel(
        organization_id=organization.id,
        telephony_configuration_id=config.id,
        address="+15551234567",
        address_normalized="+15551234567",
        address_type="phone",
        is_active=True,
    )
    async_session.add(phone_number)
    await async_session.flush()

    assert (
        await db_session.find_active_phone_number_for_inbound(
            organization.id,
            "+15551234567",
            "twilio",
        )
        is None
    )
    assert (
        await db_session.find_inbound_route_by_account(
            provider="twilio",
            account_id_field="account_sid",
            account_id="AC-parked",
            to_number="+15551234567",
            organization_id=organization.id,
        )
        is None
    )
