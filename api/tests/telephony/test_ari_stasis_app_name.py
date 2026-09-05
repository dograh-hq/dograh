"""Stasis application naming for ARI configurations.

Asterisk hands a Stasis application to whichever ARI WebSocket registered for it
last and silently stops delivering events to the previous holder. Two Dograh
configurations naming the same application on one PBX therefore do not both
work: one goes deaf with no error on either side, and the survivor receives the
other's calls — stamping its own organization into the media-socket URL, which
is what turns a live call into ``Workflow run N not found for org M``.

Uniqueness cannot be validated into a customer-chosen string, because the
colliding party may be a tenant, a self-hosted install, or another deployment
that our database cannot see. So the name is generated instead of asked for.
"""

import pytest

from api.routes.organization import _credentials_for_display
from api.services.telephony import registry
from api.services.telephony.ari_manager import ARIConnection
from api.services.telephony.providers.ari import (
    _config_loader,
    _preprocess_credentials_on_save,
)
from api.services.telephony.providers.ari.provider import ARIProvider

BASE = {
    "ari_endpoint": "http://pbx.example.com:8088",
    "app_name": "dograh",
    "app_password": "s3cr3t",
    "ws_client_name": "dograh",
}


def _legacy(**overrides):
    """A configuration written before the Stasis name was split off app_name."""
    return {**BASE, **overrides}


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_generates_a_stasis_app_name():
    created = await _preprocess_credentials_on_save(_legacy(), None)

    assert created["stasis_app_name"].startswith("dograh_")
    assert created["stasis_app_name"] != created["app_name"]


@pytest.mark.asyncio
async def test_two_configurations_never_share_a_generated_name():
    """The whole point: two customers both typing "dograh" must not collide."""
    first = await _preprocess_credentials_on_save(_legacy(), None)
    second = await _preprocess_credentials_on_save(_legacy(), None)

    assert first["stasis_app_name"] != second["stasis_app_name"]


@pytest.mark.asyncio
async def test_update_preserves_the_assigned_name():
    """The customer's dialplan routes into this name; an edit must not move it.

    Saving replaces the stored credentials wholesale, and the request schema
    does not carry the field, so an update that did not restate it would drop
    it from the row.
    """
    stored = await _preprocess_credentials_on_save(_legacy(), None)

    updated = await _preprocess_credentials_on_save(
        _legacy(app_password="rotated"), stored
    )

    assert updated["stasis_app_name"] == stored["stasis_app_name"]


@pytest.mark.asyncio
async def test_editing_a_pre_split_configuration_does_not_mint_a_name():
    """A generated name here would point at an application their dialplan
    never routes calls into — breaking a working PBX on an unrelated edit."""
    updated = await _preprocess_credentials_on_save(
        _legacy(app_password="rotated"), _legacy()
    )

    assert "stasis_app_name" not in updated


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_generated_name_is_returned_to_the_customer():
    """Server-managed fields are hidden by default; this one has to be readable
    because the customer pastes it into their own extensions.conf."""
    displayed = _credentials_for_display(
        "ari", _legacy(stasis_app_name="dograh_a1b2c3d4e5f6")
    )

    assert displayed["stasis_app_name"] == "dograh_a1b2c3d4e5f6"
    assert displayed["app_password"] != "s3cr3t", "password must still be masked"


def test_stasis_app_name_is_not_accepted_from_the_client():
    """Two independent reasons a client cannot choose its own application."""
    spec = registry.get("ari")
    assert "stasis_app_name" not in spec.config_request_cls.model_fields

    # ...and even a smuggled value does not survive request validation.
    submitted = spec.config_request_cls(
        provider="ari",
        ari_endpoint="http://pbx.example.com:8088",
        app_name="dograh",
        app_password="s3cr3t",
        stasis_app_name="dograh_someone_elses_app",
    )
    assert "stasis_app_name" not in submitted.model_dump()


@pytest.mark.asyncio
async def test_client_supplied_name_is_overwritten_on_create():
    created = await _preprocess_credentials_on_save(
        _legacy(stasis_app_name="dograh_someone_elses_app"), None
    )

    assert created["stasis_app_name"] != "dograh_someone_elses_app"


# --------------------------------------------------------------------------
# The two names address different things
# --------------------------------------------------------------------------


def test_websocket_authenticates_as_the_ari_user_and_subscribes_to_the_app():
    connection = ARIConnection(
        8628,
        1709,
        "http://pbx.example.com:8088",
        "dograh",
        "s3cr3t",
        "dograh",
        None,
        "dograh_a1b2c3d4e5f6",
    )

    assert "api_key=dograh:s3cr3t" in connection.ws_url
    assert "app=dograh_a1b2c3d4e5f6" in connection.ws_url


def test_origination_and_external_media_use_the_stasis_app():
    provider = ARIProvider(
        _config_loader(_legacy(stasis_app_name="dograh_a1b2c3d4e5f6"))
    )

    assert provider.stasis_app_name == "dograh_a1b2c3d4e5f6"
    # Authentication is unchanged — it is the ari.conf section name.
    assert provider._get_auth().login == "dograh"


# --------------------------------------------------------------------------
# Pre-split configurations keep working untouched
# --------------------------------------------------------------------------


def test_pre_split_configuration_still_uses_app_name_for_both():
    loaded = _config_loader(_legacy())
    connection = ARIConnection(
        8434, 1397, "http://pbx.example.com:8088", "dograh", "s3cr3t", "dograh"
    )

    assert loaded["stasis_app_name"] == "dograh"
    assert ARIProvider(loaded).stasis_app_name == "dograh"
    assert "app=dograh&" in connection.ws_url
