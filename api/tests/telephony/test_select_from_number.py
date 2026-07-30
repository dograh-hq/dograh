"""Caller-ID selection for one-off outbound calls.

Preference order: explicit from_number > the config's default caller ID >
random pick from the pool. Campaign dispatch always passes an explicit number
from its rotation pool, so the default must never override an explicit choice.
"""

from api.services.telephony.providers.twilio.provider import TwilioProvider


def _provider(**overrides) -> TwilioProvider:
    config = {
        "account_sid": "AC123",
        "auth_token": "twilio-auth-token",
        "from_numbers": ["+15551230001", "+15551230002", "+15551230003"],
        **overrides,
    }
    return TwilioProvider(config)


def test_explicit_from_number_wins_over_default():
    provider = _provider(default_from_number="+15551230002")
    assert provider.select_from_number("+15551230003") == "+15551230003"


def test_default_caller_id_used_when_no_explicit_number():
    provider = _provider(default_from_number="+15551230002")
    assert provider.select_from_number() == "+15551230002"
    assert provider.select_from_number(None) == "+15551230002"


def test_random_pick_from_pool_when_no_default():
    provider = _provider()
    assert provider.default_from_number is None
    for _ in range(20):
        assert provider.select_from_number() in provider.from_numbers


def test_returns_none_when_pool_empty_and_no_default():
    provider = _provider(from_numbers=[])
    assert provider.select_from_number() is None
