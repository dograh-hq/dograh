import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.enums import WorkflowRunState
from api.routes.telephony import _handle_telephony_websocket, handle_inbound_run
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
    websocket.query_params = {"token": "stream-token"}

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
        ) as shared_handler,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.redeem_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as redeem,
    ):
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    redeem.assert_awaited_once_with(7, 11, 13, "stream-token")
    websocket.accept.assert_awaited_once_with(subprotocol="audio.drachtio.org")
    shared_handler.assert_awaited_once_with(
        websocket, 7, 11, 13, provider_route_authenticated=True
    )


@pytest.mark.asyncio
async def test_websocket_rejects_invalid_capability_before_accepting():
    websocket = AsyncMock()
    websocket.query_params = {"token": "invalid"}

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
        ) as shared_handler,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.redeem_stream_token",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    websocket.accept.assert_not_awaited()
    websocket.close.assert_awaited_once_with(
        code=4401, reason="Invalid stream capability"
    )
    shared_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_websocket_rejects_tryvox_before_starting_run():
    websocket = AsyncMock()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        state=WorkflowRunState.INITIALIZED.value,
        mode="tryvox",
        initial_context={"provider": "tryvox"},
        gathered_context={"call_id": "call-123"},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    provider = _provider()

    with (
        patch("api.routes.telephony.db_client") as shared_db,
        patch(
            "api.routes.telephony.get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    ):
        shared_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        shared_db.get_workflow = AsyncMock(return_value=workflow)
        shared_db.update_workflow_run = AsyncMock()

        await _handle_telephony_websocket(websocket, 7, 11, 13)

    websocket.close.assert_awaited_once_with(
        code=4401, reason="Provider-specific WebSocket authentication required"
    )
    shared_db.update_workflow_run.assert_not_awaited()


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
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider."
            "tryvox_security.issue_stream_token",
            new_callable=AsyncMock,
            return_value="stream-token",
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        response = await handle_tryvox_answer(request, 7, 13, 11)

    payload = json.loads(response.body)
    assert payload["instructions"][0]["verb"] == "Stream"
    assert payload["instructions"][0]["track"] == "inbound_track"


@pytest.mark.asyncio
async def test_duplicate_answer_after_stream_redemption_returns_terminal_voxml():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/answer",
        {"call_uuid": "call-123", "account_id": "TJaccount"},
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_callback",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider."
            "tryvox_security.issue_stream_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        response = await handle_tryvox_answer(request, 7, 13, 11)

    payload = json.loads(response.body)
    assert payload["instructions"][-1] == {"verb": "Hangup"}
    assert all(
        instruction["verb"] != "Stream" for instruction in payload["instructions"]
    )


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
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        result = await handle_tryvox_status(request, 13)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_status_callback_is_acknowledged_without_reprocessing():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/status/13",
        {"CallUUID": "call-123", "Status": "hangup"},
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            f"{ROUTES_MODULE}._process_status_update",
            new_callable=AsyncMock,
        ) as process_status,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_callback",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        result = await handle_tryvox_status(request, 13)

    assert result == {"status": "success", "duplicate": True}
    process_status.assert_not_awaited()


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
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        with pytest.raises(HTTPException) as exc:
            await handle_tryvox_answer(request, 7, 13, 11)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unmatched_inbound_route_returns_tryvox_voxml_json():
    request = _request(
        "/api/v1/telephony/inbound/run",
        {
            "call_uuid": "call-123",
            "account_id": "TJaccount",
            "from": "+15551230002",
            "to": "+15551230001",
            "direction": "inbound",
        },
    )
    normalized = SimpleNamespace(
        provider="tryvox",
        direction="inbound",
        to_number="+15551230001",
        from_number="+15551230002",
        to_country=None,
        from_country=None,
        account_id="TJaccount",
        call_id="call-123",
        raw_data={},
    )

    with (
        patch(
            "api.routes.telephony.parse_webhook_request",
            new=AsyncMock(return_value=({}, "raw-body")),
        ),
        patch(
            "api.routes.telephony._detect_provider",
            new=AsyncMock(return_value=TryVoxProvider),
        ),
        patch(
            "api.routes.telephony.normalize_webhook_data",
            return_value=normalized,
        ),
        patch("api.routes.telephony.db_client") as shared_db,
    ):
        shared_db.find_inbound_route_by_account = AsyncMock(return_value=None)
        response = await handle_inbound_run(request)

    payload = json.loads(response.body)
    assert response.media_type == "application/json"
    assert payload["voxml_version"] == "1.0"
    assert payload["instructions"][-1] == {"verb": "Hangup"}
