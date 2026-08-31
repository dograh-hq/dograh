"""Trunks as rows, and phone numbers attached to them.

A configuration holds the credentials for a provider account; a trunk is one
carrier path through that account. The reason the association exists is that a
carrier rejects — or declines to attest — a caller ID it does not own, so the
caller ID and the route a call takes cannot be chosen from separate pools.
"""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.db.models import OrganizationModel, TelephonyTrunkModel
from api.routes.organization import (
    create_telephony_trunk,
    delete_telephony_trunk,
    get_telephony_configuration_by_id,
    list_telephony_trunks,
    update_telephony_trunk,
)
from api.schemas.telephony_config import TrunkCreateRequest, TrunkUpdateRequest
from api.services.telephony import registry
from api.services.telephony.base import TelephonyProvider
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider

SETTINGS = {"region": "India", "sip_domain": "sip.acme.example"}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def _organization(async_session):
    organization = OrganizationModel(provider_id=f"trunks-{uuid4()}")
    async_session.add(organization)
    await async_session.flush()
    return organization


async def _config(db_session, organization, name="Cloudonix", provider="cloudonix"):
    return await db_session.create_telephony_configuration(
        organization_id=organization.id,
        name=name,
        provider=provider,
        credentials={},
    )


def test_trunk_names_are_unique_within_a_configuration_but_not_across_them():
    """Cloudonix scopes trunk names to a domain, and each configuration is its
    own domain, so the constraint is per configuration rather than per org."""
    constraint = next(
        c
        for c in TelephonyTrunkModel.__table__.constraints
        if c.name == "uq_telephony_trunks_config_name"
    )

    assert [column.name for column in constraint.columns] == [
        "telephony_configuration_id",
        "name",
    ]


@pytest.mark.asyncio
async def test_two_configurations_can_hold_a_trunk_of_the_same_name(
    async_session, db_session
):
    organization = await _organization(async_session)
    first = await _config(db_session, organization, name="A")
    second = await _config(db_session, organization, name="B")

    await db_session.create_trunk(
        telephony_configuration_id=first.id, name="carrier", settings=SETTINGS
    )
    await db_session.create_trunk(
        telephony_configuration_id=second.id, name="carrier", settings=SETTINGS
    )

    assert len(await db_session.list_trunks_for_config(first.id)) == 1
    assert len(await db_session.list_trunks_for_config(second.id)) == 1


@pytest.mark.asyncio
async def test_a_number_carries_the_trunk_it_dials_out_over(async_session, db_session):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    trunk = await db_session.create_trunk(
        telephony_configuration_id=config.id, name="carrier", settings=SETTINGS
    )

    number = await db_session.create_phone_number(
        organization_id=organization.id,
        telephony_configuration_id=config.id,
        address="+14155550100",
        telephony_trunk_id=trunk.id,
    )

    assert number.telephony_trunk_id == trunk.id
    assert await db_session.get_trunk_ids_by_address_for_config(config.id) == {
        "+14155550100": trunk.id
    }
    assert await db_session.count_phone_numbers_for_trunk(trunk.id) == 1


@pytest.mark.asyncio
async def test_deleting_a_trunk_detaches_its_numbers_rather_than_deleting_them(
    async_session, db_session
):
    """SET NULL, not RESTRICT: deleting a configuration cascades to trunks and
    numbers alike, and a RESTRICT could fire against rows that same cascade is
    about to remove. The route layer is what refuses a deliberate delete."""
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    trunk = await db_session.create_trunk(
        telephony_configuration_id=config.id, name="carrier", settings=SETTINGS
    )
    number = await db_session.create_phone_number(
        organization_id=organization.id,
        telephony_configuration_id=config.id,
        address="+14155550101",
        telephony_trunk_id=trunk.id,
    )

    assert await db_session.delete_trunk(trunk.id, config.id) is True

    refreshed = await db_session.get_phone_number_for_config(number.id, config.id)
    assert refreshed is not None
    assert refreshed.telephony_trunk_id is None


@pytest.mark.asyncio
async def test_trunk_reads_and_writes_are_scoped_to_their_configuration(
    async_session, db_session
):
    organization = await _organization(async_session)
    mine = await _config(db_session, organization, name="Mine")
    theirs = await _config(db_session, organization, name="Theirs")
    trunk = await db_session.create_trunk(
        telephony_configuration_id=theirs.id, name="carrier", settings=SETTINGS
    )

    assert await db_session.get_trunk_for_config(trunk.id, mine.id) is None
    assert await db_session.delete_trunk(trunk.id, mine.id) is False
    assert (
        await db_session.update_trunk(
            trunk_id=trunk.id, telephony_configuration_id=mine.id, name="renamed"
        )
        is None
    )


@pytest.mark.asyncio
async def test_update_writes_only_the_fields_it_was_given(async_session, db_session):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    trunk = await db_session.create_trunk(
        telephony_configuration_id=config.id,
        name="carrier",
        settings=SETTINGS,
        external_id="remote-1",
    )

    renamed = await db_session.update_trunk(
        trunk_id=trunk.id, telephony_configuration_id=config.id, name="renamed"
    )

    assert renamed.name == "renamed"
    assert renamed.enabled is True
    assert renamed.settings == SETTINGS
    # A rename must not orphan the provider-side trunk.
    assert renamed.external_id == "remote-1"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _user(organization_id: int):
    return SimpleNamespace(selected_organization_id=organization_id)


@contextmanager
def _cloudonix_hooks(**overrides):
    """Swap Cloudonix's remote trunk hooks for the duration of a test.

    ``ProviderSpec`` is frozen, so the spec is rebuilt and the registry lookup
    the routes use is pointed at the copy.
    """
    original = registry.get_optional
    patched = replace(registry.get("cloudonix"), **overrides)

    def get_optional(name):
        return patched if name == "cloudonix" else original(name)

    with patch("api.routes.organization.telephony_registry.get_optional", get_optional):
        yield


@pytest.mark.asyncio
async def test_trunk_endpoints_reject_providers_without_trunks(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization, name="Twilio", provider="twilio")

    with (
        patch("api.routes.organization.db_client", db_session),
        pytest.raises(HTTPException) as excinfo,
    ):
        await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="carrier", settings=SETTINGS),
            _user(organization.id),
        )

    assert excinfo.value.status_code == 400
    assert "does not model trunks" in excinfo.value.detail


@pytest.mark.asyncio
async def test_creating_a_trunk_provisions_it_before_the_row_lands(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    apply = AsyncMock(return_value="remote-uuid")

    with (
        patch("api.routes.organization.db_client", db_session),
        _cloudonix_hooks(apply_trunk_on_save=apply),
    ):
        response = await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="acme-carrier", settings=SETTINGS),
            _user(organization.id),
        )

    assert response.name == "acme-carrier"
    assert response.settings == SETTINGS
    apply.assert_awaited_once()
    stored = await db_session.get_trunk_for_config(response.id, config.id)
    assert stored.external_id == "remote-uuid"


@pytest.mark.asyncio
async def test_a_provider_that_refuses_the_trunk_leaves_no_row_behind(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    apply = AsyncMock(
        side_effect=HTTPException(status_code=502, detail="Cloudonix down")
    )

    with (
        patch("api.routes.organization.db_client", db_session),
        _cloudonix_hooks(apply_trunk_on_save=apply),
        pytest.raises(HTTPException) as excinfo,
    ):
        await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="acme-carrier", settings=SETTINGS),
            _user(organization.id),
        )

    assert excinfo.value.status_code == 502
    assert await db_session.list_trunks_for_config(config.id) == []


@pytest.mark.asyncio
async def test_invalid_trunk_settings_are_rejected_by_the_provider_schema(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)

    with (
        patch("api.routes.organization.db_client", db_session),
        pytest.raises(HTTPException) as excinfo,
    ):
        await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="acme-carrier", settings={"region": "Mars"}),
            _user(organization.id),
        )

    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_deleting_a_trunk_in_use_is_refused_with_the_number_count(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    apply = AsyncMock(return_value="remote-uuid")
    remove = AsyncMock()

    with (
        patch("api.routes.organization.db_client", db_session),
        _cloudonix_hooks(apply_trunk_on_save=apply, remove_trunk_on_delete=remove),
    ):
        trunk = await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="acme-carrier", settings=SETTINGS),
            _user(organization.id),
        )
        await db_session.create_phone_number(
            organization_id=organization.id,
            telephony_configuration_id=config.id,
            address="+14155550102",
            telephony_trunk_id=trunk.id,
        )

        with pytest.raises(HTTPException) as excinfo:
            await delete_telephony_trunk(config.id, trunk.id, _user(organization.id))

    assert excinfo.value.status_code == 409
    assert "1 phone number(s)" in excinfo.value.detail
    # Nothing was torn down remotely for a delete we refused.
    remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_renaming_onto_another_trunks_name_is_refused_before_provisioning(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    apply = AsyncMock(return_value="remote-uuid")

    with (
        patch("api.routes.organization.db_client", db_session),
        _cloudonix_hooks(apply_trunk_on_save=apply),
    ):
        await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="first", settings=SETTINGS),
            _user(organization.id),
        )
        second = await create_telephony_trunk(
            config.id,
            TrunkCreateRequest(name="second", settings=SETTINGS),
            _user(organization.id),
        )
        apply.reset_mock()

        with pytest.raises(HTTPException) as excinfo:
            await update_telephony_trunk(
                config.id,
                second.id,
                TrunkUpdateRequest(name="first"),
                _user(organization.id),
            )

    assert excinfo.value.status_code == 409
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_listing_reports_how_many_numbers_ride_each_trunk(
    async_session, db_session
):
    organization = await _organization(async_session)
    config = await _config(db_session, organization)
    busy = await db_session.create_trunk(
        telephony_configuration_id=config.id, name="busy", settings=SETTINGS
    )
    await db_session.create_trunk(
        telephony_configuration_id=config.id, name="idle", settings=SETTINGS
    )
    await db_session.create_phone_number(
        organization_id=organization.id,
        telephony_configuration_id=config.id,
        address="+14155550103",
        telephony_trunk_id=busy.id,
    )

    with patch("api.routes.organization.db_client", db_session):
        response = await list_telephony_trunks(config.id, _user(organization.id))

    assert {t.name: t.phone_number_count for t in response.trunks} == {
        "busy": 1,
        "idle": 0,
    }


@pytest.mark.asyncio
async def test_the_detail_response_says_whether_trunks_apply_at_all(
    async_session, db_session
):
    """An empty ``trunks`` list means two different things.

    On Cloudonix it means "none added yet" and the UI should offer to add one;
    on Twilio it means "this integration has no trunks" and the UI should not
    mention them. The flag is what tells those apart.
    """
    organization = await _organization(async_session)
    sip = await _config(db_session, organization)
    api = await _config(db_session, organization, name="Twilio", provider="twilio")

    with patch("api.routes.organization.db_client", db_session):
        sip_detail = await get_telephony_configuration_by_id(
            sip.id, _user(organization.id)
        )
        api_detail = await get_telephony_configuration_by_id(
            api.id, _user(organization.id)
        )

    assert (sip_detail.supports_trunks, sip_detail.trunks) == (True, [])
    assert (api_detail.supports_trunks, api_detail.trunks) == (False, [])


# ---------------------------------------------------------------------------
# Call path
# ---------------------------------------------------------------------------


def _provider(trunks, trunk_id_by_number) -> TelephonyProvider:
    return CloudonixProvider(
        {
            "bearer_token": "token",
            "domain_id": "acme.cloudonix.net",
            "trunks": trunks,
            "trunk_id_by_number": trunk_id_by_number,
            "from_numbers": list(trunk_id_by_number),
        }
    )


def test_a_call_goes_out_on_the_trunk_that_authorised_its_caller_id():
    """The failure this prevents: presenting carrier B's DID to carrier A."""
    provider = _provider(
        trunks=[
            {"id": 1, "name": "carrier-a", "enabled": True},
            {"id": 2, "name": "carrier-b", "enabled": True},
        ],
        trunk_id_by_number={"+14155550110": 1, "+442079460111": 2},
    )

    assert provider.select_trunk("+14155550110")["name"] == "carrier-a"
    assert provider.select_trunk("+442079460111")["name"] == "carrier-b"


def test_an_unassigned_number_falls_back_only_when_there_is_one_trunk():
    sole = _provider(
        trunks=[{"id": 1, "name": "carrier-a", "enabled": True}],
        trunk_id_by_number={"+14155550110": None},
    )
    assert sole.select_trunk("+14155550110")["name"] == "carrier-a"

    # With several, guessing is exactly the bug — go out unpinned instead.
    several = _provider(
        trunks=[
            {"id": 1, "name": "carrier-a", "enabled": True},
            {"id": 2, "name": "carrier-b", "enabled": True},
        ],
        trunk_id_by_number={"+14155550110": None},
    )
    assert several.select_trunk("+14155550110") is None


def test_a_number_pinned_to_a_disabled_trunk_is_not_rerouted():
    """Falling back to another trunk would present a caller ID that carrier
    does not own — the mismatch the assignment exists to prevent."""
    provider = _provider(
        trunks=[
            {"id": 1, "name": "carrier-a", "enabled": False},
            {"id": 2, "name": "carrier-b", "enabled": True},
        ],
        trunk_id_by_number={"+14155550110": 1},
    )

    assert provider.select_trunk("+14155550110") is None


def test_a_provider_without_trunks_never_pins():
    provider = _provider(trunks=[], trunk_id_by_number={})

    assert provider.select_trunk("+14155550110") is None
    assert provider.select_trunk(None) is None
