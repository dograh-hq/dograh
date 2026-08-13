import pytest

from api.utils.telephony_address import normalize_telephony_address


class TestPstnNormalization:
    """PSTN inputs, with and without an India country hint."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Already E.164 — the hint must be a no-op.
            ("+919262175513", "+919262175513"),
            ("919262175513", "+919262175513"),
            # National format with the trunk-prefix zero.
            ("09262175513", "+919262175513"),
            ("02271264296", "+912271264296"),
            ("08071582014", "+918071582014"),
            # Bare national significant number.
            ("9262175513", "+919262175513"),
        ],
    )
    def test_india_hint(self, raw, expected):
        assert normalize_telephony_address(raw, country_hint="IN").canonical == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # "00" is the ITU international access prefix — the country code
            # already follows, so the hint must not be applied on top.
            ("00919262175513", "+919262175513"),
            ("00918071582014", "+918071582014"),
            # A non-Indian number reached through the same access prefix.
            ("00442079460958", "+442079460958"),
        ],
    )
    def test_international_access_prefix_is_not_double_prefixed(self, raw, expected):
        """Regression: "0091..." used to normalize to "+9191...".

        `_normalize_pstn` tested `digits.startswith(dial)` before stripping the
        leading zeros, so the access prefix hid the country code and the dial
        code was prepended a second time. The resulting address matched no
        configured phone number and inbound calls were rejected with
        PHONE_NUMBER_NOT_CONFIGURED.
        """
        assert normalize_telephony_address(raw, country_hint="IN").canonical == expected

    def test_country_code_claimed_only_when_dial_code_matches(self):
        """An explicitly international number need not be in the hinted country."""
        indian = normalize_telephony_address("00919262175513", country_hint="IN")
        assert indian.country_code == "IN"

        british = normalize_telephony_address("00442079460958", country_hint="IN")
        assert british.country_code is None

    def test_no_hint_leaves_digits_untouched(self):
        result = normalize_telephony_address("919262175513")
        assert result.canonical == "+919262175513"
        assert result.country_code is None

    def test_separators_are_stripped(self):
        assert (
            normalize_telephony_address("+91 92621-75513").canonical
            == "+919262175513"
        )

    def test_address_type_is_pstn(self):
        assert normalize_telephony_address("919262175513").address_type == "pstn"


class TestSipUriNormalization:
    def test_default_port_dropped_and_host_lowercased(self):
        result = normalize_telephony_address("sip:Alice@Example.COM:5060")
        assert result.canonical == "sip:Alice@example.com"
        assert result.address_type == "sip_uri"

    def test_non_default_port_preserved(self):
        assert (
            normalize_telephony_address("sip:alice@example.com:5080").canonical
            == "sip:alice@example.com:5080"
        )

    def test_sips_default_port_dropped(self):
        assert (
            normalize_telephony_address("sips:alice@example.com:5061").canonical
            == "sips:alice@example.com"
        )


class TestExtensionsAndInvalidInput:
    def test_short_extension_is_not_pstn(self):
        result = normalize_telephony_address("1001")
        assert result.canonical == "1001"
        assert result.address_type == "sip_extension"

    def test_alphanumeric_username(self):
        result = normalize_telephony_address("Support-Queue")
        assert result.canonical == "support-queue"
        assert result.address_type == "sip_extension"

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_rejected(self, raw):
        with pytest.raises(ValueError):
            normalize_telephony_address(raw)

    def test_none_rejected(self):
        with pytest.raises(ValueError):
            normalize_telephony_address(None)
