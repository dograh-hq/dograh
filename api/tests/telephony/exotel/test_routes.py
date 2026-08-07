"""Status callback route tests for Exotel.

Copy into dograh as: api/tests/telephony/exotel/test_routes.py
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from api.services.telephony.providers.exotel.provider import ExotelProvider
from api.services.telephony.providers.exotel.routes import handle_exotel_status_callback


def _provider() -> ExotelProvider:
    return ExotelProvider(
        {
            "account_sid": "exotelaccount",
            "api_key": "key123",
            "api_token": "token456",
            "from_numbers": ["+9180XXXXXXX1"],
        }
    )


def _form_request(path: str, form_data: dict[str, str]) -> Request:
    body = urlencode(form_data).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        },
        receive,
    )


def _json_request(path: str, payload) -> Request:
    body = (
        payload
        if isinstance(payload, (bytes, bytearray))
        else json.dumps(payload).encode("utf-8")
    )

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_status_callback_happy_path():
    provider = _provider()
    workflow_run = SimpleNamespace(id=42, workflow_id=7)
    workflow = SimpleNamespace(id=7, organization_id=9)
    request = _form_request(
        "/api/v1/telephony/exotel/status-callback/42",
        {
            "CallSid": "call-1",
            "Status": "completed",
            "From": "+919999999999",
            "To": "+9180XXXXXXX1",
            "Duration": "15",
        },
    )

    with (
        patch(
            "api.services.telephony.providers.exotel.routes.db_client"
        ) as db_client,
        patch(
            "api.services.telephony.providers.exotel.routes.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.exotel.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)

        result = await handle_exotel_status_callback(42, request)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    args = process_status.await_args.args
    assert args[0] == 42
    assert args[1].call_id == "call-1"
    assert args[1].status == "completed"


@pytest.mark.asyncio
async def test_status_callback_malformed_json_does_not_500():
    request = _json_request(
        "/api/v1/telephony/exotel/status-callback/42",
        b"{not-json",
    )

    result = await handle_exotel_status_callback(42, request)
    assert result["status"] == "ignored"
    assert result["reason"] == "malformed_body"


@pytest.mark.asyncio
async def test_status_callback_missing_run_ignored():
    request = _form_request(
        "/api/v1/telephony/exotel/status-callback/99",
        {"CallSid": "call-1", "Status": "completed"},
    )

    with patch(
        "api.services.telephony.providers.exotel.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_id = AsyncMock(return_value=None)
        result = await handle_exotel_status_callback(99, request)

    assert result == {"status": "ignored", "reason": "workflow_run_not_found"}
