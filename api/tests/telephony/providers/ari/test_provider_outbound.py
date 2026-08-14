"""Unit tests for ARIProvider outbound SIP endpoint formation.

Covers the fix for GAP-G6 in deploy/asterisk/MIGRATION_PLAN.md:
bare PSTN numbers must be dialed as ``PJSIP/<digits>@<trunk>`` rather than
``PJSIP/<digits>`` so they route through the configured Verimor PJSIP
endpoint.
"""

import pytest

from api.services.telephony.providers.ari.provider import ARIProvider


def _provider(**overrides) -> ARIProvider:
    config = {
        "ari_endpoint": "http://asterisk.example.com:8088",
        "app_name": "dograh",
        "app_password": "secret",
        "from_numbers": [],
        "pjsip_outbound_endpoint": "verimor",
    }
    config.update(overrides)
    return ARIProvider(config)


class TestNormalizeSipEndpoint:
    """Tests for _normalize_sip_endpoint — the outbound trunk-routing fix."""

    @pytest.mark.parametrize(
        "to_number, expected",
        [
            # E.164 Turkish mobile -> digits with @verimor
            ("+905551234567", "PJSIP/905551234567@verimor"),
            # National format with leading 0 -> preserves national form
            ("05551234567", "PJSIP/05551234567@verimor"),
            # Bare 10-digit subscriber number (no country code)
            ("5551234567", "PJSIP/5551234567@verimor"),
            # Number with spaces
            ("+90 555 123 45 67", "PJSIP/905551234567@verimor"),
        ],
    )
    def test_pstn_numbers_route_through_trunk(self, to_number, expected):
        provider = _provider()
        assert provider._normalize_sip_endpoint(to_number) == expected

    def test_sip_uri_passed_through_verbatim(self):
        provider = _provider()
        assert provider._normalize_sip_endpoint("SIP/6001@verimor") == "SIP/6001@verimor"
        assert provider._normalize_sip_endpoint("PJSIP/6001@verimor") == "PJSIP/6001@verimor"

    def test_bare_extension_dialed_without_trunk(self):
        """Short numeric extensions that aren't PSTN are treated as local
        PJSIP devices (no @trunk suffix) — preserves legacy behavior."""
        provider = _provider()
        result = provider._normalize_sip_endpoint("8000")
        assert result == "PJSIP/8000"

    def test_custom_outbound_endpoint(self):
        """The trunk name is configurable per-tenant."""
        provider = _provider(pjsip_outbound_endpoint="trunk_eu")
        result = provider._normalize_sip_endpoint("+905551234567")
        assert result == "PJSIP/905551234567@trunk_eu"

    def test_turkish_national_zero_format_is_opt_in(self):
        """National dialing is available without changing the safe default."""
        provider = _provider(outbound_number_format="national_zero")
        result = provider._normalize_sip_endpoint("+905551234567")
        assert result == "PJSIP/05551234567@verimor"

    def test_e164_format_remains_default(self):
        provider = _provider()
        result = provider._normalize_sip_endpoint("+905551234567")
        assert result == "PJSIP/905551234567@verimor"

    def test_no_plus_prefix_on_digits(self):
        """The PJSIP device name must not contain a leading '+'. """
        provider = _provider()
        result = provider._normalize_sip_endpoint("+905551234567")
        assert not result.split("/")[1].startswith("+")
        assert "@verimor" in result
