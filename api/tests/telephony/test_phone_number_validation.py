from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organization import create_phone_number
from api.schemas.telephony_phone_number import PhoneNumberCreateRequest
from api.services.telephony.base import (
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
)
from api.services.telephony.providers.ari.provider import ARIProvider
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.plivo.provider import PlivoProvider
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.twilio.provider import TwilioProvider
from api.services.telephony.providers.vobiz.provider import VobizProvider
from api.services.telephony.providers.vonage.provider import VonageProvider


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
    def __init__(self, responses: list[_StubResponse]):
        self.responses = responses
        self.requests: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_twilio_validation_requires_an_exact_owned_number():
    provider = TwilioProvider(
        {
            "account_sid": "AC123",
            "auth_token": "token",
            "from_numbers": [],
        }
    )
    session = _StubSession(
        [
            _StubResponse(
                200,
                data={
                    "incoming_phone_numbers": [
                        {"sid": "PN_PARTIAL", "phone_number": "+155512300020"},
                        {"sid": "PN_EXACT", "phone_number": "+15551230002"},
                    ]
                },
            )
        ]
    )

    with patch(
        "api.services.telephony.providers.twilio.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert result.ok
    assert session.requests[0][1]["params"] == {"PhoneNumber": "+15551230002"}


@pytest.mark.asyncio
async def test_twilio_validation_rejects_unowned_number():
    provider = TwilioProvider(
        {
            "account_sid": "AC123",
            "auth_token": "token",
            "from_numbers": [],
        }
    )
    provider._lookup_incoming_number_sid = AsyncMock(return_value=None)

    result = await provider.validate_phone_number("+15551230002")

    assert not result.ok
    assert "not owned by this Twilio account" in result.message


@pytest.mark.asyncio
async def test_twilio_missing_number_is_an_idempotent_detach():
    provider = TwilioProvider(
        {
            "account_sid": "AC123",
            "auth_token": "token",
            "from_numbers": [],
        }
    )
    provider._lookup_incoming_number_sid = AsyncMock(return_value=None)

    result = await provider.configure_inbound("+15551230002", None)

    assert result.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected_ok", [(200, True), (404, False)])
async def test_plivo_validation_uses_account_number_resource(status, expected_ok):
    provider = PlivoProvider(
        {"auth_id": "MA123", "auth_token": "token", "from_numbers": []}
    )
    session = _StubSession([_StubResponse(status)])

    with patch(
        "api.services.telephony.providers.plivo.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert result.ok is expected_ok
    assert session.requests[0][0].endswith("/Account/MA123/Number/15551230002/")


@pytest.mark.asyncio
async def test_vonage_validation_exact_matches_owned_number():
    provider = VonageProvider(
        {
            "api_key": "key",
            "api_secret": "secret",
            "application_id": "app-id",
            "private_key": "unused",
            "from_numbers": [],
        }
    )
    session = _StubSession(
        [_StubResponse(200, data={"numbers": [{"msisdn": "15551230002"}]})]
    )

    with patch(
        "api.services.telephony.providers.vonage.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert result.ok
    assert session.requests[0][0] == "https://rest.nexmo.com/account/numbers"
    assert session.requests[0][1]["params"] == {
        "pattern": "15551230002",
        "search_pattern": 0,
        "size": 100,
    }


@pytest.mark.asyncio
async def test_vonage_validation_rejects_nonexact_search_result():
    provider = VonageProvider(
        {
            "api_key": "key",
            "api_secret": "secret",
            "application_id": "app-id",
            "private_key": "unused",
            "from_numbers": [],
        }
    )
    session = _StubSession(
        [_StubResponse(200, data={"numbers": [{"msisdn": "155512300020"}]})]
    )

    with patch(
        "api.services.telephony.providers.vonage.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert not result.ok


@pytest.mark.asyncio
async def test_telnyx_validation_rejects_filter_results_without_exact_number():
    provider = TelnyxProvider(
        {
            "api_key": "key",
            "connection_id": "connection-id",
            "from_numbers": [],
        }
    )
    session = _StubSession(
        [
            _StubResponse(
                200,
                data={"data": [{"phone_number": "+155512300020"}]},
            )
        ]
    )

    with patch(
        "api.services.telephony.providers.telnyx.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert not result.ok
    assert session.requests[0][1]["params"]["filter[phone_number]"] == ("+15551230002")


@pytest.mark.asyncio
async def test_vobiz_validation_paginates_account_inventory():
    provider = VobizProvider(
        {"auth_id": "MA123", "auth_token": "token", "from_numbers": []}
    )
    session = _StubSession(
        [
            _StubResponse(
                200,
                data={
                    "items": [{"auth_id": "MA123", "e164": "+15551230001"}],
                    "page": 1,
                    "per_page": 1,
                    "total": 2,
                },
            ),
            _StubResponse(
                200,
                data={
                    "items": [{"auth_id": "MA123", "e164": "+15551230002"}],
                    "page": 2,
                    "per_page": 1,
                    "total": 2,
                },
            ),
        ]
    )

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert result.ok
    assert [request[1]["params"]["page"] for request in session.requests] == [1, 2]
    assert all(
        request[1]["params"]["search"] == "+15551230002" for request in session.requests
    )


@pytest.mark.asyncio
async def test_vobiz_validation_rejects_number_missing_from_account_inventory():
    provider = VobizProvider(
        {"auth_id": "MA123", "auth_token": "token", "from_numbers": []}
    )
    session = _StubSession(
        [
            _StubResponse(
                200,
                data={"items": [], "page": 1, "per_page": 25, "total": 0},
            )
        ]
    )

    with patch(
        "api.services.telephony.providers.vobiz.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert not result.ok


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, data, expected_ok",
    [
        (200, {"source": "+15551230002"}, True),
        (404, None, False),
    ],
)
async def test_cloudonix_validation_uses_domain_dnid(status, data, expected_ok):
    provider = CloudonixProvider(
        {
            "bearer_token": "token",
            "domain_id": "example.cloudonix.net",
            "from_numbers": [],
        }
    )
    session = _StubSession([_StubResponse(status, data=data)])

    with patch(
        "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+15551230002")

    assert result.ok is expected_ok
    assert session.requests[0][0].endswith(
        "/domains/example.cloudonix.net/dnids/%2B15551230002"
    )


@pytest.mark.asyncio
async def test_ari_validation_explicitly_accepts_pbx_managed_address():
    provider = ARIProvider(
        {
            "ari_endpoint": "http://asterisk:8088",
            "app_name": "dograh",
            "app_password": "secret",
            "from_numbers": [],
        }
    )

    result = await provider.validate_phone_number("PJSIP/office-trunk")

    assert result.ok


@pytest.mark.asyncio
async def test_create_rejects_unowned_number_before_database_insert():
    config = SimpleNamespace(provider="twilio", credentials={"account_sid": "AC123"})
    provider = SimpleNamespace(
        validate_phone_number=AsyncMock(
            return_value=ProviderSyncResult(ok=False, message="not owned")
        )
    )
    create_local = AsyncMock()
    find_conflict = AsyncMock()

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch(
            "api.routes.organization.get_telephony_provider_by_id",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.routes.organization.db_client.find_inbound_routing_conflict",
            find_conflict,
        ),
        patch("api.routes.organization.db_client.create_phone_number", create_local),
        pytest.raises(HTTPException) as exc_info,
    ):
        await create_phone_number(
            config_id=7,
            request=PhoneNumberCreateRequest(address="+15551230002"),
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "not owned"
    find_conflict.assert_not_awaited()
    create_local.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_surfaces_provider_lookup_failure_as_bad_gateway():
    config = SimpleNamespace(provider="twilio", credentials={"account_sid": "AC123"})
    provider = SimpleNamespace(
        validate_phone_number=AsyncMock(
            side_effect=ProviderPhoneNumberLookupError("Twilio API 401")
        )
    )

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch(
            "api.routes.organization.get_telephony_provider_by_id",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await create_phone_number(
            config_id=7,
            request=PhoneNumberCreateRequest(address="+15551230002"),
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Twilio API 401"


@pytest.mark.asyncio
async def test_create_validates_country_hinted_number_in_canonical_form():
    config = SimpleNamespace(provider="twilio", credentials={"account_sid": "AC123"})
    provider = SimpleNamespace(
        validate_phone_number=AsyncMock(
            return_value=ProviderSyncResult(ok=False, message="not owned")
        )
    )

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch(
            "api.routes.organization.get_telephony_provider_by_id",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        pytest.raises(HTTPException),
    ):
        await create_phone_number(
            config_id=7,
            request=PhoneNumberCreateRequest(
                address="080 4307 1383", country_code="IN"
            ),
            user=SimpleNamespace(selected_organization_id=11),
        )

    provider.validate_phone_number.assert_awaited_once_with("+918043071383")
