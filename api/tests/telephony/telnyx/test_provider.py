import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nacl.signing
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.telnyx.routes import handle_telnyx_events


def _body() -> str:
    return json.dumps(
        {
            "data": {
                "record_type": "event",
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "call-control-id",
                    "connection_id": "connection-id",
                    "direction": "incoming",
                    "from": "+15551230001",
                    "to": "+15551230002",
                },
            }
        },
        separators=(",", ":"),
    )


def _provider(public_key: str = "") -> TelnyxProvider:
    return TelnyxProvider(
        {
            "api_key": "placeholder-api-key",
            "connection_id": "connection-id",
            "webhook_public_key": public_key,
            "from_numbers": ["+15551230002"],
        }
    )


def _signed_headers(body: str, timestamp: str | None = None):
    if timestamp is None:
        timestamp = str(int(time.time()))
    signing_key = nacl.signing.SigningKey.generate()
    public_key = base64.b64encode(bytes(signing_key.verify_key)).decode("ascii")
    signed_payload = f"{timestamp}|{body}".encode("utf-8")
    signature = base64.b64encode(signing_key.sign(signed_payload).signature).decode(
        "ascii"
    )
    return (
        public_key,
        {
            "telnyx-signature-ed25519": signature,
            "telnyx-timestamp": timestamp,
        },
    )


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
            "path": "/api/v1/telephony/telnyx/events/123",
            "headers": [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in headers.items()
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_valid_telnyx_signature():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_tampered_body():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body.replace("incoming", "outgoing"),
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_signature_header():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {"telnyx-timestamp": headers["telnyx-timestamp"]},
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_timestamp_header():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {"telnyx-signature-ed25519": headers["telnyx-signature-ed25519"]},
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_missing_config_public_key():
    body = _body()
    _, headers = _signed_headers(body)
    provider = _provider()

    # REMOVE-AFTER 2026-05-15: drop the patch wrapper once
    # TELNYX_WEBHOOK_VERIFICATION_OPTIONAL is removed; the bare call below
    # will then assert the only path.
    with patch(
        "api.services.telephony.providers.telnyx.provider.TELNYX_WEBHOOK_VERIFICATION_OPTIONAL",
        False,
    ):
        result = await provider.verify_inbound_signature(
            "https://example.test/api/v1/telephony/inbound/run",
            json.loads(body),
            headers,
            body,
        )

    assert result is False


# REMOVE-AFTER 2026-05-15: delete this whole test along with the
# TELNYX_WEBHOOK_VERIFICATION_OPTIONAL flag.
@pytest.mark.asyncio
async def test_verify_inbound_signature_allows_missing_key_when_optional_flag_set():
    body = _body()
    _, headers = _signed_headers(body)
    provider = _provider()

    with patch(
        "api.services.telephony.providers.telnyx.provider.TELNYX_WEBHOOK_VERIFICATION_OPTIONAL",
        True,
    ):
        result = await provider.verify_inbound_signature(
            "https://example.test/api/v1/telephony/inbound/run",
            json.loads(body),
            headers,
            body,
        )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_reads_headers_case_insensitively():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        {
            "Telnyx-Signature-Ed25519": headers["telnyx-signature-ed25519"],
            "Telnyx-Timestamp": headers["telnyx-timestamp"],
        },
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_telnyx_events_route_verifies_signature_before_status_update():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        result = await handle_telnyx_events(
            _request(body, headers), workflow_run_id=123
        )

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_stale_timestamp():
    body = _body()
    stale_ts = str(int(time.time()) - 600)
    public_key, headers = _signed_headers(body, timestamp=stale_ts)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_future_timestamp():
    body = _body()
    future_ts = str(int(time.time()) + 600)
    public_key, headers = _signed_headers(body, timestamp=future_ts)
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_non_integer_timestamp():
    body = _body()
    public_key, headers = _signed_headers(body)
    headers["telnyx-timestamp"] = "not-a-number"
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_wrong_length_public_key():
    body = _body()
    _, headers = _signed_headers(body)
    short_key = base64.b64encode(b"x" * 16).decode("ascii")
    provider = _provider(short_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_wrong_length_signature():
    body = _body()
    public_key, headers = _signed_headers(body)
    headers["telnyx-signature-ed25519"] = base64.b64encode(b"x" * 32).decode("ascii")
    provider = _provider(public_key)

    result = await provider.verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        json.loads(body),
        headers,
        body,
    )

    assert result is False


@pytest.mark.asyncio
async def test_telnyx_events_route_rejects_invalid_signature_with_401():
    body = _body()
    public_key, headers = _signed_headers(body)
    provider = _provider(public_key)

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client") as db_client,
        patch(
            "api.services.telephony.providers.telnyx.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.telnyx.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
        patch.object(
            provider,
            "verify_inbound_signature",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(
            return_value=SimpleNamespace(workflow_id=7)
        )
        db_client.get_workflow_by_id = AsyncMock(
            return_value=SimpleNamespace(organization_id=11)
        )

        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(_request(body, headers), workflow_run_id=123)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid webhook signature"
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_telnyx_events_route_rejects_invalid_utf8_body_with_400():
    invalid_body = b"\xff\xfe\xfd"

    async def receive():
        return {"type": "http.request", "body": invalid_body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telephony/telnyx/events/123",
            "headers": [],
        },
        receive,
    )

    with pytest.raises(HTTPException) as exc_info:
        await handle_telnyx_events(request, workflow_run_id=123)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Webhook body is not valid UTF-8"
