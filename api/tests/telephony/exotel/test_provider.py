"""Unit tests for Exotel telephony provider (Connect Voice AI + inbound).

Copy into dograh as: api/tests/telephony/exotel/test_provider.py
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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
    assert "wss://example.test/api/v1/telephony/ws/7/9/42" in body
    assert "<Stream" in body
    assert "status-callback/42" in body
    assert response.media_type == "application/xml"


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
    assert result.caller_number == "+9180XXXXXXX1"

    _, kwargs = session.post.call_args
    form = kwargs["data"]
    assert form["From"] == "+919999999999"
    assert form["CallerId"] == "+9180XXXXXXX1"
    assert form["StreamType"] == "bidirectional"
    assert form["StreamUrl"] == "wss://api.example.test/api/v1/telephony/ws/7/9/42"
    assert form["StatusCallback"].endswith("/exotel/status-callback/42")
    assert form["StatusCallbackEvents[]"] == "terminal"


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
