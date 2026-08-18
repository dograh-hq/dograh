"""Unit tests for Exotel telephony provider (Connect Voice AI + inbound)."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.services.telephony.providers.exotel.config import ExotelConfigurationRequest
from api.services.telephony.providers.exotel.provider import ExotelProvider


def _provider(**overrides) -> ExotelProvider:
    config = {
        "account_sid": "exotelaccount",
        "api_key": "key123",
        "api_token": "token456",
        "api_base_url": "https://api.in.exotel.com",
        "from_numbers": ["+9180XXXXXXX1"],
    }
    config.update(overrides)
    return ExotelProvider(config)


def _basic_auth_header(api_key: str, api_token: str) -> str:
    token = base64.b64encode(f"{api_key}:{api_token}".encode()).decode()
    return f"Basic {token}"


INBOUND_FIXTURE = {
    "CallSid": "call-inbound-1",
    "AccountSid": "exotelaccount",
    "From": "+919876543210",
    "To": "+9180XXXXXXX1",
    "Direction": "incoming",
    "CallStatus": "ringing",
}


def test_validate_config_requires_credentials():
    assert _provider().validate_config() is True
    assert _provider(api_token=None).validate_config() is False


def test_api_base_url_rejects_non_exotel_origin():
    with pytest.raises(ValueError):
        _provider(api_base_url="https://evil.example.com")
    with pytest.raises(ValidationError):
        ExotelConfigurationRequest(
            account_sid="a",
            api_key="k",
            api_token="t",
            api_base_url="https://evil.example.com",
        )


def test_can_handle_webhook_exotel_user_agent():
    assert ExotelProvider.can_handle_webhook(
        {},
        {"user-agent": "Exotel/1.0"},
    )


def test_can_handle_webhook_form_fields():
    assert ExotelProvider.can_handle_webhook(INBOUND_FIXTURE, {})


def test_can_handle_webhook_rejects_twilio_account():
    assert not ExotelProvider.can_handle_webhook(
        {
            "CallSid": "CA123",
            "AccountSid": "ACffffffffffffffffffffffffffffffff",
            "From": "+15551230001",
            "To": "+15551230002",
            "ApiVersion": "2010-04-01",
        },
        {},
    )


def test_parse_inbound_webhook():
    parsed = ExotelProvider.parse_inbound_webhook(INBOUND_FIXTURE)
    assert parsed.call_id == "call-inbound-1"
    assert parsed.account_id == "exotelaccount"
    assert parsed.from_number == "+919876543210"
    assert parsed.to_number == "+9180XXXXXXX1"
    assert parsed.direction == "inbound"


def test_parse_inbound_webhook_sets_india_hint_for_national_to():
    parsed = ExotelProvider.parse_inbound_webhook(
        {
            "CallSid": "call-inbound-2",
            "AccountSid": "exotelaccount",
            "From": "07007586339",
            "To": "07314852338",
            "Direction": "incoming",
        }
    )
    assert parsed.to_number == "07314852338"
    assert parsed.to_country == "IN"
    assert parsed.direction == "inbound"


def test_validate_account_id():
    assert ExotelProvider.validate_account_id(
        {"account_sid": "exotelaccount"}, "exotelaccount"
    )
    assert not ExotelProvider.validate_account_id(
        {"account_sid": "exotelaccount"}, "other"
    )


@pytest.mark.asyncio
async def test_verify_inbound_signature_requires_basic_auth():
    provider = _provider()
    assert not await provider.verify_inbound_signature(
        "https://example.test/inbound", INBOUND_FIXTURE, {}
    )
    assert await provider.verify_inbound_signature(
        "https://example.test/inbound",
        INBOUND_FIXTURE,
        {"Authorization": _basic_auth_header("key123", "token456")},
    )
    assert not await provider.verify_inbound_signature(
        "https://example.test/inbound",
        INBOUND_FIXTURE,
        {"Authorization": _basic_auth_header("wrong", "token456")},
    )


@pytest.mark.asyncio
async def test_verify_status_callback_token():
    provider = _provider()
    url = provider.build_status_callback_url("https://example.test", 42)
    assert await provider.verify_inbound_signature(url, {}, {})
    assert not await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/exotel/status-callback/42"
        "?exotel_auth=deadbeef",
        {},
        {},
    )


@pytest.mark.asyncio
async def test_verify_status_callback_token_rejects_non_ascii():
    provider = _provider()
    assert not await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/exotel/status-callback/42"
        "?exotel_auth=%C3%A9vil",
        {},
        {},
    )


@pytest.mark.asyncio
async def test_start_inbound_stream_contains_ws_url():
    provider = _provider()
    response = await provider.start_inbound_stream(
        websocket_url="wss://example.test/api/v1/telephony/ws/7/9/42",
        workflow_run_id=42,
        normalized_data=ExotelProvider.parse_inbound_webhook(INBOUND_FIXTURE),
        backend_endpoint="https://example.test",
    )
    body = response.body.decode() if hasattr(response, "body") else response.content
    if isinstance(body, bytes):
        body = body.decode()
    assert json.loads(body) == {"url": "wss://example.test/api/v1/telephony/ws/7/9/42"}
    assert response.media_type == "application/json"


@pytest.mark.asyncio
async def test_initiate_call_posts_connect_with_stream_url():
    provider = _provider()

    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(
        return_value=json.dumps(
            {"Call": {"Sid": "call-sid-1", "Status": "in-progress"}}
        )
    )
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.exotel.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
    ):
        result = await provider.initiate_call(
            to_number="+919999999999",
            webhook_url="https://unused.example.test",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert result.call_id == "call-sid-1"
    # India E.164 is dialed to Exotel as 0-prefixed national.
    assert result.caller_number == "080XXXXXXX1"

    _, kwargs = session.post.call_args
    auth = kwargs["auth"]
    assert auth.login == "key123"
    assert auth.password == "token456"
    form = kwargs["data"]
    assert form["From"] == "09999999999"
    assert form["CallerId"] == "080XXXXXXX1"
    assert form["StreamType"] == "bidirectional"
    assert form["StreamUrl"] == "wss://api.example.test/api/v1/telephony/ws/7/9/42"
    assert form["StatusCallback"].startswith(
        "https://api.example.test/api/v1/telephony/exotel/status-callback/42"
    )
    assert "exotel_auth=" in form["StatusCallback"]
    assert form["StatusCallbackEvents[]"] == "terminal"


@pytest.mark.asyncio
async def test_initiate_call_uses_default_from_number():
    provider = _provider(
        from_numbers=["+918011111111", "+918022222222"],
        default_from_number="+918022222222",
    )

    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(
        return_value=json.dumps({"Call": {"Sid": "call-sid-2", "Status": "queued"}})
    )
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.exotel.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
    ):
        result = await provider.initiate_call(
            to_number="+919999999999",
            webhook_url="https://unused.example.test",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert result.caller_number == "08022222222"
    _, kwargs = session.post.call_args
    assert kwargs["data"]["CallerId"] == "08022222222"


@pytest.mark.asyncio
async def test_initiate_call_stream_url_includes_token_when_secret_set():
    provider = _provider()

    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(
        return_value=json.dumps({"Call": {"Sid": "call-sid-3", "Status": "queued"}})
    )
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.exotel.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
        patch(
            "api.services.telephony.ws_auth.constants.TELEPHONY_WS_TOKEN_SECRET",
            "test-secret",
        ),
    ):
        await provider.initiate_call(
            to_number="+919999999999",
            webhook_url="https://unused.example.test",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    _, kwargs = session.post.call_args
    stream_url = kwargs["data"]["StreamUrl"]
    assert stream_url.startswith(
        "wss://api.example.test/api/v1/telephony/ws/7/9/42/"
    )
    assert len(stream_url.rsplit("/", 1)[-1]) == 64


def test_exotel_dial_number_india_national():
    assert ExotelProvider._exotel_dial_number("+917314852338") == "07314852338"
    assert ExotelProvider._exotel_dial_number("07314852338") == "07314852338"


def test_number_match_keys_align_e164_and_national():
    e164 = ExotelProvider._number_match_keys("+917314852338", "IN")
    national = ExotelProvider._number_match_keys("07314852338", "IN")
    assert e164 & national


def test_iter_incoming_phone_entries_unwraps_nested():
    payload = {
        "incoming_phone_numbers": [
            {"phone_number": "07314852338"},
            {"friendly_name": "08045680765"},
        ]
    }
    entries = ExotelProvider._iter_incoming_phone_entries(payload)
    assert [e.get("phone_number") or e.get("friendly_name") for e in entries] == [
        "07314852338",
        "08045680765",
    ]


def test_iter_incoming_phone_entries_unwraps_legacy_nested():
    payload = {
        "IncomingPhoneNumbers": [
            {"IncomingPhoneNumber": {"PhoneNumber": "07314852338"}},
            {"PhoneNumber": "08045680765"},
        ]
    }
    entries = ExotelProvider._iter_incoming_phone_entries(payload)
    assert [e["PhoneNumber"] for e in entries] == ["07314852338", "08045680765"]


@pytest.mark.asyncio
async def test_validate_phone_number_matches_nested_national_format():
    provider = _provider()
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(
        return_value={
            "incoming_phone_numbers": [
                {"phone_number": "07314852338"},
            ]
        }
    )
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+917314852338")

    assert result.ok is True
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_validate_phone_number_not_owned():
    provider = _provider()
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(
        return_value={
            "incoming_phone_numbers": [
                {"phone_number": "08011111111"},
            ]
        }
    )
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.validate_phone_number("+917314852338")

    assert result.ok is False
    assert session.get.call_count == 1


@pytest.mark.asyncio
async def test_initiate_call_raises_on_api_error():
    provider = _provider()

    response = MagicMock()
    response.status = 400
    response.text = AsyncMock(return_value="bad request")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "api.services.telephony.providers.exotel.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.exotel.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", "wss://api.example.test"),
        ),
        pytest.raises(HTTPException),
    ):
        await provider.initiate_call(
            to_number="+919999999999",
            webhook_url="https://unused",
            workflow_run_id=1,
            workflow_id=1,
            organization_id=1,
        )


def test_parse_status_callback():
    parsed = _provider().parse_status_callback(
        {
            "CallSid": "call-1",
            "Status": "completed",
            "From": "+911",
            "To": "+912",
            "Duration": "12",
        }
    )
    assert parsed["call_id"] == "call-1"
    assert parsed["status"] == "completed"
    assert parsed["duration"] == "12"


def test_supports_transfers_false():
    assert _provider().supports_transfers() is False
