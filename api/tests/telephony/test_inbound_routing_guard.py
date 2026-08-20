"""The inbound routing key must stay unambiguous across every save path.

The rule lives in ``api.services.telephony.inbound_routing``; these cover the
rule itself and the configuration-update route, where one account id is shared
by every phone number on the configuration.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organization import update_telephony_configuration
from api.schemas.telephony_config import TelephonyConfigurationUpdateRequest
from api.services.telephony.inbound_routing import (
    InboundRoutingConflictError,
    assert_no_inbound_routing_conflict,
    canonical_address,
    routing_account_id,
)
from api.services.telephony.providers.twilio.config import TwilioConfigurationRequest


def _config(*, id=1, organization_id=11, name="Twilio prod"):
    return SimpleNamespace(id=id, organization_id=organization_id, name=name)


def _phone(*, address="+19789911885"):
    return SimpleNamespace(address=address, address_normalized=address)


def _db(conflicts=()):
    return SimpleNamespace(
        find_inbound_routing_conflicts=AsyncMock(return_value=list(conflicts))
    )


# --- the routing key itself -------------------------------------------------


def test_routing_account_id_reads_the_providers_account_field():
    assert routing_account_id("twilio", {"account_sid": "AC123"}) == "AC123"
    assert (
        routing_account_id("cloudonix", {"domain_id": "acme.cx.net"}) == "acme.cx.net"
    )


def test_routing_account_id_is_none_when_unset_or_provider_has_no_account():
    assert routing_account_id("twilio", {}) is None
    assert routing_account_id("twilio", {"account_sid": ""}) is None
    assert routing_account_id("twilio", None) is None
    # ARI has no account concept; its inbound path is keyed differently.
    assert routing_account_id("ari", {"app_name": "dograh"}) is None
    assert routing_account_id("nonexistent-provider", {"account_sid": "AC1"}) is None


def test_canonical_address_matches_the_stored_normalized_form():
    assert canonical_address("+1 (978) 991-1885") == "+19789911885"
    assert canonical_address("080 4307 1383", "IN") == "+918043071383"


# --- when the guard declines to run ----------------------------------------


@pytest.mark.asyncio
async def test_no_query_for_a_provider_without_an_account_field():
    db = _db()
    await assert_no_inbound_routing_conflict(
        provider="ari",
        credentials={"app_name": "dograh"},
        addresses=["+19789911885"],
        organization_id=11,
        db=db,
    )
    db.find_inbound_routing_conflicts.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_query_when_the_account_id_is_not_set_yet():
    db = _db()
    await assert_no_inbound_routing_conflict(
        provider="twilio",
        credentials={"auth_token": "secret"},
        addresses=["+19789911885"],
        organization_id=11,
        db=db,
    )
    db.find_inbound_routing_conflicts.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_query_when_the_account_id_is_unchanged():
    """A rename or a secret rotation must not be blocked by a collision that
    already exists in the data and that this save does nothing to worsen."""
    db = _db(conflicts=[(_config(), _phone())])
    await assert_no_inbound_routing_conflict(
        provider="twilio",
        credentials={"account_sid": "AC123", "auth_token": "rotated"},
        previous_credentials={"account_sid": "AC123", "auth_token": "old"},
        addresses=["+19789911885"],
        organization_id=11,
        db=db,
    )
    db.find_inbound_routing_conflicts.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_query_when_the_configuration_owns_no_numbers():
    db = _db()
    await assert_no_inbound_routing_conflict(
        provider="twilio",
        credentials={"account_sid": "AC123"},
        addresses=[],
        organization_id=11,
        db=db,
    )
    db.find_inbound_routing_conflicts.assert_not_awaited()


# --- when it fires ----------------------------------------------------------


@pytest.mark.asyncio
async def test_conflict_in_the_same_org_names_the_other_configuration():
    db = _db(conflicts=[(_config(name="Twilio staging"), _phone())])
    with pytest.raises(InboundRoutingConflictError) as exc:
        await assert_no_inbound_routing_conflict(
            provider="twilio",
            credentials={"account_sid": "AC123"},
            addresses=["+19789911885"],
            organization_id=11,
            db=db,
        )
    assert "Twilio staging" in str(exc.value)
    assert exc.value.same_organization is True


@pytest.mark.asyncio
async def test_conflict_in_another_org_does_not_leak_who_owns_it():
    db = _db(conflicts=[(_config(organization_id=1976, name="Their Twilio"), _phone())])
    with pytest.raises(InboundRoutingConflictError) as exc:
        await assert_no_inbound_routing_conflict(
            provider="twilio",
            credentials={"account_sid": "AC123"},
            addresses=["+19789911885"],
            organization_id=11,
            db=db,
        )
    message = str(exc.value)
    assert "another organization using the same provider account" in message
    assert "Their Twilio" not in message
    assert "1976" not in message
    assert exc.value.same_organization is False


@pytest.mark.asyncio
async def test_the_configuration_being_updated_is_excluded_from_its_own_check():
    db = _db()
    await assert_no_inbound_routing_conflict(
        provider="twilio",
        credentials={"account_sid": "AC123"},
        addresses=["+19789911885", "+17089052818"],
        organization_id=11,
        exclude_configuration_id=7,
        db=db,
    )
    db.find_inbound_routing_conflicts.assert_awaited_once()
    kwargs = db.find_inbound_routing_conflicts.await_args.kwargs
    assert kwargs["exclude_configuration_id"] == 7
    assert kwargs["account_id_field"] == "account_sid"
    assert kwargs["account_id"] == "AC123"
    assert kwargs["addresses_normalized"] == ["+19789911885", "+17089052818"]


# --- the configuration-update route -----------------------------------------


def _update_request(account_sid: str, auth_token: str = "tok"):
    return TelephonyConfigurationUpdateRequest(
        config=TwilioConfigurationRequest(
            account_sid=account_sid, auth_token=auth_token
        )
    )


@pytest.mark.asyncio
async def test_update_cannot_repoint_a_config_at_an_account_that_owns_the_number():
    """Adding a number under your own account and then repointing that account
    at another org's must not take over their inbound routing."""
    existing = SimpleNamespace(
        id=7,
        organization_id=11,
        provider="twilio",
        credentials={"account_sid": "ACmine", "auth_token": "tok"},
    )
    update = AsyncMock()

    with (
        patch(
            "api.routes.organization.db_client.get_telephony_configuration_for_org",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "api.routes.organization.db_client.list_phone_numbers_for_config",
            new_callable=AsyncMock,
            return_value=[_phone()],
        ),
        patch(
            "api.routes.organization.db_client.find_inbound_routing_conflicts",
            new_callable=AsyncMock,
            return_value=[(_config(id=3, organization_id=7, name="Victim"), _phone())],
        ),
        patch(
            "api.routes.organization.db_client.update_telephony_configuration", update
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await update_telephony_configuration(
            config_id=7,
            request=_update_request("ACvictim"),
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert exc.value.status_code == 409
    assert "already registered" in exc.value.detail
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_that_keeps_the_same_account_is_not_blocked():
    """A configuration that already shares a routing key with another row must
    still accept edits that leave its account id alone, or existing collisions
    would make the rows involved uneditable."""
    existing = SimpleNamespace(
        id=7,
        organization_id=11,
        provider="twilio",
        credentials={"account_sid": "ACmine", "auth_token": "old"},
    )
    updated = SimpleNamespace(id=7)
    conflicts = AsyncMock(return_value=[(_config(), _phone())])

    with (
        patch(
            "api.routes.organization.db_client.get_telephony_configuration_for_org",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "api.routes.organization.db_client.list_phone_numbers_for_config",
            new_callable=AsyncMock,
            return_value=[_phone()],
        ),
        patch(
            "api.routes.organization.db_client.find_inbound_routing_conflicts",
            conflicts,
        ),
        patch(
            "api.routes.organization.db_client.update_telephony_configuration",
            new_callable=AsyncMock,
            return_value=updated,
        ) as update,
        patch(
            "api.routes.organization._detail_response",
            new_callable=AsyncMock,
            return_value="detail",
        ),
    ):
        result = await update_telephony_configuration(
            config_id=7,
            request=_update_request("ACmine", auth_token="rotated"),
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert result == "detail"
    update.assert_awaited_once()
    conflicts.assert_not_awaited()
