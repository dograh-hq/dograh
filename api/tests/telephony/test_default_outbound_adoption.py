"""Which configuration an organization treats as its default outbound.

The default is the customer's choice and nothing picks one on their behalf.
Creating a configuration stores exactly the flag it was given, and adding a
phone number never touches the configuration row at all — the storage layer
does what it was asked and no more.

This used to be implicit: the first configuration an organization created
became its default. That silently handed the flag to the SIP configuration
Dograh provisions at signup — the one row guaranteed not to be able to place a
call — and then refused to move it when the customer added a real provider.
"""

from uuid import uuid4

import pytest

from api.db.models import OrganizationModel


async def _organization(async_session):
    organization = OrganizationModel(provider_id=f"default-outbound-{uuid4()}")
    async_session.add(organization)
    await async_session.flush()
    return organization


async def _config(db_session, organization, name, provider, **kwargs):
    return await db_session.create_telephony_configuration(
        organization_id=organization.id,
        name=name,
        provider=provider,
        credentials={},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_creating_a_configuration_never_claims_the_default(
    async_session, db_session
):
    organization = await _organization(async_session)

    first = await _config(db_session, organization, "Dograh Cloudonix SIP", "cloudonix")
    second = await _config(db_session, organization, "Twilio", "twilio")

    assert first.is_default_outbound is False
    assert second.is_default_outbound is False
    assert await db_session.get_default_telephony_configuration(organization.id) is None


@pytest.mark.asyncio
async def test_the_caller_asking_for_default_gets_it(async_session, db_session):
    organization = await _organization(async_session)

    await _config(db_session, organization, "Dograh Cloudonix SIP", "cloudonix")
    chosen = await _config(
        db_session, organization, "Twilio", "twilio", is_default_outbound=True
    )

    default = await db_session.get_default_telephony_configuration(organization.id)
    assert default.id == chosen.id


@pytest.mark.asyncio
async def test_outbound_candidates_consider_the_marked_default_first(
    async_session, db_session
):
    organization = await _organization(async_session)

    first = await _config(db_session, organization, "Plivo", "plivo")
    chosen = await _config(
        db_session, organization, "Twilio", "twilio", is_default_outbound=True
    )
    third = await _config(db_session, organization, "Vobiz", "vobiz")

    candidates = await db_session.list_outbound_telephony_configuration_candidates(
        organization.id
    )

    assert [row.id for row in candidates] == [chosen.id, first.id, third.id]


@pytest.mark.asyncio
async def test_claiming_the_default_demotes_the_previous_holder(
    async_session, db_session
):
    """Two defaults would make the lookup return an arbitrary row."""
    organization = await _organization(async_session)

    first = await _config(
        db_session, organization, "Twilio", "twilio", is_default_outbound=True
    )
    second = await _config(
        db_session, organization, "Plivo", "plivo", is_default_outbound=True
    )

    default = await db_session.get_default_telephony_configuration(organization.id)
    assert default.id == second.id

    rows = await db_session.list_telephony_configurations(organization.id)
    assert [row.id for row in rows if row.is_default_outbound] == [second.id]
    assert first.id not in [row.id for row in rows if row.is_default_outbound]


@pytest.mark.asyncio
async def test_adding_a_phone_number_does_not_touch_the_configuration(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization, "Twilio", "twilio")

    await db_session.create_phone_number(
        organization_id=organization.id,
        telephony_configuration_id=config.id,
        address="+14155550123",
    )

    assert await db_session.get_default_telephony_configuration(organization.id) is None


@pytest.mark.asyncio
async def test_setting_the_default_explicitly_is_how_it_moves(
    async_session, db_session
):
    organization = await _organization(async_session)

    provisioned = await _config(
        db_session, organization, "Dograh Cloudonix SIP", "cloudonix"
    )
    customer = await _config(db_session, organization, "Twilio", "twilio")

    await db_session.set_default_telephony_configuration(customer.id, organization.id)
    default = await db_session.get_default_telephony_configuration(organization.id)
    assert default.id == customer.id

    await db_session.set_default_telephony_configuration(
        provisioned.id, organization.id
    )
    default = await db_session.get_default_telephony_configuration(organization.id)
    assert default.id == provisioned.id
