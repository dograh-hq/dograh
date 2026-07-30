import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.services.telephony.providers.tryvox.provider import TryVoxProvider
from api.services.telephony.providers.tryvox.routes import (
    handle_tryvox_answer,
    handle_tryvox_status,
    handle_tryvox_websocket,
)

ROUTES_MODULE = "api.services.telephony.providers.tryvox.routes"


def _provider() -> TryVoxProvider:
    return TryVoxProvider(
        {
            "auth_id": "TJaccount",
            "auth_token": "account-token",
            "webhook_secret": "account-webhook-secret",
            "from_numbers": ["+15551230001"],
        }
    )


def _request(path: str, body: dict) -> Request:
    raw = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"account-webhook-secret",
        f"{timestamp}.{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()

    async def receive():
        return {
            "type": "http.request",
            "body": raw.encode(),
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("dograh.test", 443),
            "path": path,
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-tryvox-timestamp", timestamp.encode()),
                (
                    b"x-tryvox-signature",
                    f"t={timestamp},v1={digest}".encode(),
                ),
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_websocket_accepts_tryvox_media_subprotocol():
    websocket = AsyncMock()

    with patch(
        "api.routes.telephony._handle_telephony_websocket",
        new_callable=AsyncMock,
    ) as shared_handler:
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    websocket.accept.assert_awaited_once_with(
        subprotocol="audio.drachtio.org"
    )
    shared_handler.assert_awaited_once_with(websocket, 7, 11, 13)


@pytest.mark.asyncio
async def test_answer_route_verifies_signature_and_returns_voxml():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/answer",
        {
            "call_uuid": "call-123",
            "account_id": "TJaccount",
            "status": "answered",
        },
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        response = await handle_tryvox_answer(request, 7, 13, 11)

    payload = json.loads(response.body)
    assert payload["instructions"][0]["verb"] == "Stream"
    assert payload["instructions"][0]["track"] == "inbound_track"


@pytest.mark.asyncio
async def test_status_route_processes_verified_callback():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/status/13",
        {
            "Event": "Status",
            "CallUUID": "call-123",
            "Status": "hangup",
            "Direction": "outbound",
        },
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.services.telephony.providers.tryvox.routes._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        result = await handle_tryvox_status(request, 13)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_answer_route_rejects_call_id_mismatch():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-expected"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/answer",
        {"call_uuid": "call-other", "account_id": "TJaccount"},
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        with pytest.raises(HTTPException) as exc:
            await handle_tryvox_answer(request, 7, 13, 11)

    assert exc.value.status_code == 403
