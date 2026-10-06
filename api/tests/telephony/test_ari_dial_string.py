"""How an ARI configuration turns a destination into a dial string.

Asterisk dials a channel technology and a route, not a number: ``PJSIP/1001``
names an endpoint called ``1001``. That is right for a PBX whose endpoints are
its extensions and wrong for every install whose endpoints are trunks, where
originating ``PJSIP/+966500000000`` fails with "Allocation failed" — so where
the number goes is configuration, not a constant.
"""

import pytest
from pydantic import ValidationError

from api.services.telephony import registry
from api.services.telephony.providers.ari import _config_loader
from api.services.telephony.providers.ari.config import ARIConfigurationRequest
from api.services.telephony.providers.ari.dial_string import (
    DEFAULT_DIAL_STRING_TEMPLATE,
    build_dial_string,
)
from api.services.telephony.providers.ari.provider import ARIProvider

BASE = {
    "ari_endpoint": "http://pbx.example.com:8088",
    "app_name": "dograh",
    "app_password": "s3cr3t",
}


def _provider(**overrides) -> ARIProvider:
    return ARIProvider(_config_loader({**BASE, **overrides}))


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_plain_number_uses_the_configured_template():
    """The trunk case from the report: a number has to reach a trunk."""
    provider = _provider(dial_string_template="PJSIP/{number}@my-trunk")
    assert (
        build_dial_string("+966500000000", provider.dial_string_template)
        == "PJSIP/+966500000000@my-trunk"
    )


def test_local_channel_template_routes_through_the_dialplan():
    """FreePBX outbound routes pick the trunk, so Dograh must not."""
    assert (
        build_dial_string("+966500000000", "Local/{number}@from-internal")
        == "Local/+966500000000@from-internal"
    )


@pytest.mark.parametrize(
    "destination",
    [
        "PJSIP/614180@testbench",
        "SIP/1001",
        "Local/1001@from-internal",
        "IAX2/peer/1001",
    ],
)
def test_a_destination_naming_its_own_technology_is_dialled_as_written(destination):
    """One row can address a specific trunk whatever the template does."""
    provider = _provider(dial_string_template="PJSIP/{number}@my-trunk")
    assert build_dial_string(destination, provider.dial_string_template) == destination


def test_configuration_without_a_template_keeps_dialling_endpoints_by_name():
    """Configurations written before templates existed must not change."""
    provider = _provider()
    assert provider.dial_string_template == DEFAULT_DIAL_STRING_TEMPLATE
    assert build_dial_string("1001", provider.dial_string_template) == "PJSIP/1001"


def test_stored_blank_template_falls_back_to_the_default():
    """A cleared field in the form is "no opinion", not an empty dial string."""
    provider = _provider(dial_string_template="")
    assert build_dial_string("1001", provider.dial_string_template) == "PJSIP/1001"


# --------------------------------------------------------------------------
# Template validation
# --------------------------------------------------------------------------


def test_template_defaults_when_unset():
    config = ARIConfigurationRequest(**BASE)
    assert config.dial_string_template == DEFAULT_DIAL_STRING_TEMPLATE


@pytest.mark.parametrize(
    "template",
    [
        "PJSIP/trunk",  # no placeholder: every call would dial one endpoint
        "PJSIP/{extension}@trunk",  # placeholder we do not substitute
        "{number}@from-internal",  # no channel technology
    ],
)
def test_a_template_that_cannot_dial_is_rejected_on_save(template):
    with pytest.raises(ValidationError):
        ARIConfigurationRequest(**BASE, dial_string_template=template)


# --------------------------------------------------------------------------
# Destination policy
# --------------------------------------------------------------------------


def test_ari_does_not_require_e164_destinations():
    """A PBX reaches extensions and SIP URIs; a carrier does not."""
    assert registry.get("ari").requires_e164_destinations is False


def test_carriers_require_e164_destinations():
    for name in ("twilio", "telnyx", "plivo", "vonage", "exotel", "vobiz"):
        assert registry.get(name).requires_e164_destinations is True, name


# --------------------------------------------------------------------------
# Transfers
# --------------------------------------------------------------------------


def test_transfer_destinations_use_the_same_template():
    """A transfer leaves over the same trunk an outbound call does."""
    provider = _provider(dial_string_template="PJSIP/{number}@my-trunk")
    assert (
        build_dial_string("+14155550123", provider.dial_string_template)
        == "PJSIP/+14155550123@my-trunk"
    )
    assert (
        build_dial_string("PJSIP/1001", provider.dial_string_template) == "PJSIP/1001"
    )
