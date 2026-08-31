"""Phone-number ownership lookups run against the org's own provider account,
so their failures must reach the log classified and user-owned.

The provider's HTTP status decides the type, and it has to travel structurally:
an auth rejection is the account holder's configuration to fix, and telling them
so must not depend on parsing the provider's error wording.
"""

from unittest.mock import AsyncMock, patch

import loguru
import pytest
from fastapi import HTTPException

from api.routes.organization import _ensure_provider_phone_number
from api.services.telephony.base import ProviderPhoneNumberLookupError
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.plivo.provider import PlivoProvider
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.twilio.provider import TwilioProvider
from api.services.telephony.providers.vobiz.provider import VobizProvider
from api.services.telephony.providers.vonage.provider import VonageProvider

_TWILIO_401_BODY = (
    '{"code":20003,"message":"Authenticate",'
    '"more_info":"https://www.twilio.com/docs/errors/20003","status":401}'
)


class _StubResponse:
    def __init__(self, status: int, *, data=None, body: str = ""):
        self.status = status
        self._data = data
        self._body = body

    async def json(self):
        return self._data

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StubSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url: str, **kwargs):
        return self.responses.pop(0)

    def post(self, url: str, **kwargs):
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _capture():
    records = []
    handler_id = loguru.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    return records, handler_id


def _failure_records(records: list) -> list:
    return [r for r in records if r["extra"].get("error_code")]


async def _ensure_with(provider) -> tuple[HTTPException, list]:
    records, handler_id = _capture()
    try:
        with (
            patch(
                "api.routes.organization.get_telephony_provider_by_id",
                AsyncMock(return_value=provider),
            ),
            pytest.raises(HTTPException) as excinfo,
        ):
            await _ensure_provider_phone_number(953, 1, "+17372508034")
    finally:
        loguru.logger.remove(handler_id)
    return excinfo.value, _failure_records(records)


async def test_auth_rejection_is_a_user_config_error():
    """A Twilio 401 is bad customer credentials, not a Dograh fault."""
    provider = TwilioProvider(
        {"account_sid": "AC123", "auth_token": "token", "from_numbers": []}
    )
    session = _StubSession([_StubResponse(401, body=_TWILIO_401_BODY)])

    with patch(
        "api.services.telephony.providers.twilio.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        http_exc, failures = await _ensure_with(provider)

    assert http_exc.status_code == 502
    assert len(failures) == 1
    extra = failures[0]["extra"]
    assert extra["error_owner"] == "user"
    assert extra["error_source"] == "telephony"
    assert extra["error_type"] == "config_error"
    assert extra["error_code"] == "twilio-401"
    assert extra["provider"] == "twilio"
    assert extra["retryable"] is False
    assert extra["telephony_configuration_id"] == 953
    # What the customer is told has to point at their credentials, not at us.
    assert "configuration and credentials" in extra["external_message"]


async def test_provider_outage_is_a_retryable_provider_error():
    """A 500 is the provider's problem, and worth retrying — unlike a 401."""
    provider = TwilioProvider(
        {"account_sid": "AC123", "auth_token": "token", "from_numbers": []}
    )
    session = _StubSession([_StubResponse(500, body="upstream boom")])

    with patch(
        "api.services.telephony.providers.twilio.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        _, failures = await _ensure_with(provider)

    extra = failures[0]["extra"]
    assert extra["error_type"] == "provider_error"
    assert extra["error_code"] == "twilio-500"
    assert extra["retryable"] is True


async def test_transport_failure_classifies_off_the_chained_cause():
    """No response means no status; the wrapped transport error carries the shape."""
    provider = TwilioProvider(
        {"account_sid": "AC123", "auth_token": "token", "from_numbers": []}
    )
    provider._lookup_incoming_number_sid = AsyncMock(
        side_effect=ConnectionError("DNS failure")
    )

    _, failures = await _ensure_with(provider)

    extra = failures[0]["extra"]
    assert extra["error_type"] == "provider_error"
    assert extra["retryable"] is True
    assert extra["error_owner"] == "user"


def _provider_cases():
    """Each provider with the module path of the aiohttp it should be stubbed on."""
    return [
        (
            TwilioProvider(
                {"account_sid": "AC123", "auth_token": "token", "from_numbers": []}
            ),
            "twilio",
        ),
        (
            PlivoProvider(
                {"auth_id": "MA123", "auth_token": "token", "from_numbers": []}
            ),
            "plivo",
        ),
        (
            TelnyxProvider(
                {"api_key": "key", "connection_id": "cid", "from_numbers": []}
            ),
            "telnyx",
        ),
        (
            VonageProvider(
                {
                    "api_key": "key",
                    "api_secret": "secret",
                    "application_id": "app-id",
                    "private_key": "unused",
                    "from_numbers": [],
                }
            ),
            "vonage",
        ),
        (
            VobizProvider(
                {"auth_id": "MA123", "auth_token": "token", "from_numbers": []}
            ),
            "vobiz",
        ),
        (
            CloudonixProvider(
                {
                    "bearer_token": "token",
                    "domain_id": "example.cloudonix.net",
                    "from_numbers": [],
                }
            ),
            "cloudonix",
        ),
    ]


@pytest.mark.parametrize("provider,module", _provider_cases())
async def test_every_provider_attaches_the_http_status(provider, module):
    """Without the status the failure degrades to an unattributable system error."""
    session = _StubSession([_StubResponse(401, body="denied")])

    with (
        patch(
            f"api.services.telephony.providers.{module}.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        pytest.raises(ProviderPhoneNumberLookupError) as excinfo,
    ):
        await provider.validate_phone_number("+17372508034")

    assert excinfo.value.status_code == 401
