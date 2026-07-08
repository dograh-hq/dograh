import hashlib
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony.providers.vonage.provider import VonageProvider
from api.services.telephony.providers.vonage.routes import handle_vonage_events

SIGNATURE_SECRET = "vonage-signature-secret"


def _body() -> str:
    return json.dumps(
        {
            "from": "15551230001",
            "to": "15551230002",
            "uuid": "aaaaaaaa-bbbb-cccc-dddd-0123456789ab",
            "conversation_uuid": "CON-aaaaaaaa-bbbb-cccc-dddd-0123456789ab",
            "status": "answered",
            "direction": "inbound",
        },
        separators=(",", ":"),
    )


def _provider(**overrides) -> VonageProvider:
    config = {
        "api_key": "vonage-api-key",
        "api_secret": "vonage-api-secret",
        "application_id": "aaaaaaaa-bbbb-cccc-dddd-0123456789ab",
        "private_key": "placeholder-private-key",
        "signature_secret": SIGNATURE_SECRET,
        "from_numbers": ["15551230002"],
    }
    config.update(overrides)
    return VonageProvider(config)


def _signed_headers(
    body: str,
    *,
    signature_secret: str = SIGNATURE_SECRET,
    api_key: str = "vonage-api-key",
    application_id: str = "aaaaaaaa-bbbb-cccc-dddd-0123456789ab",
) -> dict[str, str]:
    token = jwt.encode(
        {
            "iat": int(time.time()),
            "jti": "test-jti",
            "iss": "Vonage",
            "payload_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "api_key": api_key,
            "application_id": application_id,
        },
        signature_secret,
        algorithm="HS256",
    )
    return {"authorization": f"Bearer {token}"}


def _request(body: str, headers: dict[str, str]) -> Request:
    async def receive():
        return {
            "type": "http.request",
            "body": body.encode("utf-8"),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/vonage/events/123",
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in headers.items()
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_valid_vonage_signed_webhook():
    body = _body()
    provider = _provider()

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        _signed_headers(body),
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_tampered_payload():
    body = _body()
    provider = _provider()

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        _signed_headers(body),
        body.replace("answered", "completed"),
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_signature_secret():
    body = _body()
    provider = _provider(signature_secret=None)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        _signed_headers(body),
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_wrong_api_key_claim():
    body = _body()
    provider = _provider()

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        _signed_headers(body, api_key="other-api-key"),
        body,
    )

    assert result is False


def test_parse_inbound_webhook_uses_signed_api_key_claim_for_account_id():
    body = _body()
    normalized = VonageProvider.parse_inbound_webhook(
        json.loads(body), headers=_signed_headers(body)
    )

    assert normalized.provider == "vonage"
    assert normalized.call_id == "aaaaaaaa-bbbb-cccc-dddd-0123456789ab"
    assert normalized.account_id == "vonage-api-key"
    assert normalized.direction == "inbound"


def test_can_handle_webhook_detects_signed_vonage_answer_payload():
    body = _body()

    assert VonageProvider.can_handle_webhook(json.loads(body), _signed_headers(body))


@pytest.mark.asyncio
async def test_start_inbound_stream_returns_websocket_ncco():
    body = _body()
    provider = _provider()
    normalized = VonageProvider.parse_inbound_webhook(
        json.loads(body), headers=_signed_headers(body)
    )

    response = await provider.start_inbound_stream(
        websocket_url="wss://example.test/api/v1/telephony/ws/1/2/3",
        workflow_run_id=123,
        normalized_data=normalized,
        backend_endpoint="https://example.test",
    )

    ncco = json.loads(response.body)
    assert ncco == [
        {
            "action": "connect",
            "eventUrl": ["https://example.test/api/v1/telephony/vonage/events/123"],
            "endpoint": [
                {
                    "type": "websocket",
                    "uri": "wss://example.test/api/v1/telephony/ws/1/2/3",
                    "content-type": "audio/l16;rate=16000",
                    "headers": {
                        "workflow_run_id": "123",
                        "call_uuid": "aaaaaaaa-bbbb-cccc-dddd-0123456789ab",
                    },
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_vonage_events_route_verifies_signature_before_status_update():
    body = _body()
    provider = _provider()

    with (
        patch("api.services.telephony.providers.vonage.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vonage.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
    ):
        process_status = AsyncMock()
        status_processor = SimpleNamespace(
            StatusCallbackRequest=SimpleNamespace,
            _process_status_update=process_status,
        )
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with patch.dict(
            sys.modules,
            {"api.services.telephony.status_processor": status_processor},
        ):
            result = await handle_vonage_events(
                _request(body, _signed_headers(body)), workflow_run_id=123
            )

    assert result == {"status": "ok"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_vonage_events_route_rejects_invalid_signature_with_401():
    body = _body()
    provider = _provider()

    with (
        patch("api.services.telephony.providers.vonage.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.vonage.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
    ):
        process_status = AsyncMock()
        status_processor = SimpleNamespace(
            StatusCallbackRequest=SimpleNamespace,
            _process_status_update=process_status,
        )
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with (
            patch.dict(
                sys.modules,
                {"api.services.telephony.status_processor": status_processor},
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await handle_vonage_events(
                _request(body, _signed_headers(body, signature_secret="wrong")),
                workflow_run_id=123,
            )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid webhook signature"
    process_status.assert_not_awaited()
