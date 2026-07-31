import asyncio
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
from api.routes.telephony import handle_inbound_run, websocket_endpoint
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

    async def start_media(*args, **kwargs):
        assert await kwargs["on_provider_ready"]() is True

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
            side_effect=start_media,
        ) as shared_handler,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ) as reserve,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as consume,
        patch(f"{ROUTES_MODULE}.db_client") as route_db,
    ):
        route_db.update_workflow_run = AsyncMock()
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    reserve.assert_awaited_once_with(7, 11, 13, "stream-token")
    websocket.accept.assert_awaited_once_with(subprotocol="audio.drachtio.org")
    consume.assert_awaited_once_with(7, 11, 13, "reservation")
    route_db.update_workflow_run.assert_awaited_once_with(
        run_id=13, state=WorkflowRunState.RUNNING.value
    )
    shared_handler.assert_awaited_once()
    assert shared_handler.await_args.kwargs["provider_route_authenticated"] is True
    assert callable(shared_handler.await_args.kwargs["on_provider_ready"])


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
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    websocket.accept.assert_not_awaited()
    websocket.close.assert_awaited_once_with(
        code=4401, reason="Invalid stream capability"
    )
    shared_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_accept_failure_releases_reserved_capability():
    websocket = AsyncMock()
    websocket.query_params = {"token": "stream-token"}
    websocket.accept.side_effect = RuntimeError("handshake failed")

    with (
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as release,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
        ) as consume,
    ):
        with pytest.raises(RuntimeError, match="handshake failed"):
            await handle_tryvox_websocket(websocket, 7, 11, 13)

    release.assert_awaited_once_with(7, 11, 13, "reservation", "stream-token")
    consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_pre_media_rejection_releases_reserved_capability():
    websocket = AsyncMock()
    websocket.query_params = {"token": "stream-token"}

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as release,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
        ) as consume,
    ):
        await handle_tryvox_websocket(websocket, 7, 11, 13)

    release.assert_awaited_once_with(7, 11, 13, "reservation", "stream-token")
    consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_pre_media_cancellation_releases_reserved_capability():
    websocket = AsyncMock()
    websocket.query_params = {"token": "stream-token"}

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as release,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
        ) as consume,
    ):
        with pytest.raises(asyncio.CancelledError):
            await handle_tryvox_websocket(websocket, 7, 11, 13)

    release.assert_awaited_once_with(7, 11, 13, "reservation", "stream-token")
    consume.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_run_state_failure_restores_consumed_capability():
    websocket = AsyncMock()
    websocket.query_params = {"token": "stream-token"}

    async def fail_run_update(*args, **kwargs):
        await kwargs["on_provider_ready"]()

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
            side_effect=fail_run_update,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.rollback_consumed_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as rollback,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_stream_token",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(f"{ROUTES_MODULE}.db_client") as route_db,
    ):
        route_db.update_workflow_run = AsyncMock(side_effect=RuntimeError("DB failed"))
        with pytest.raises(RuntimeError, match="DB failed"):
            await handle_tryvox_websocket(websocket, 7, 11, 13)

    rollback.assert_awaited_once_with(7, 11, 13, "reservation", "stream-token")


@pytest.mark.asyncio
async def test_websocket_run_state_cancellation_restores_consumed_capability():
    websocket = AsyncMock()
    websocket.query_params = {"token": "stream-token"}

    async def cancel_run_update(*args, **kwargs):
        await kwargs["on_provider_ready"]()

    with (
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
            side_effect=cancel_run_update,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_stream_token",
            new_callable=AsyncMock,
            return_value="reservation",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.consume_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.rollback_consumed_stream_token",
            new_callable=AsyncMock,
            return_value=True,
        ) as rollback,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_stream_token",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(f"{ROUTES_MODULE}.db_client") as route_db,
    ):
        route_db.update_workflow_run = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await handle_tryvox_websocket(websocket, 7, 11, 13)

    rollback.assert_awaited_once_with(7, 11, 13, "reservation", "stream-token")


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

    with (
        patch("api.routes.telephony.db_client") as shared_db,
        patch(
            "api.routes.telephony._handle_telephony_websocket",
            new_callable=AsyncMock,
        ) as shared_handler,
        patch.object(
            TryVoxProvider,
            "handle_websocket",
            new_callable=AsyncMock,
        ) as provider_handler,
        patch(
            "api.services.pipecat.run_pipeline.run_pipeline_telephony",
            new_callable=AsyncMock,
        ) as run_pipeline,
    ):
        shared_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        shared_db.update_workflow_run = AsyncMock()

        await websocket_endpoint(websocket, 7, 11, 13)

    websocket.accept.assert_not_awaited()
    websocket.close.assert_awaited_once_with(
        code=4401, reason="Provider-specific WebSocket authentication required"
    )
    shared_db.update_workflow_run.assert_not_awaited()
    shared_handler.assert_not_awaited()
    provider_handler.assert_not_awaited()
    run_pipeline.assert_not_awaited()


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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
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
async def test_duplicate_answer_returns_same_unredeemed_capability():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    callback = {
        "call_uuid": "call-123",
        "account_id": "TJaccount",
        "status": "answered",
    }

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            side_effect=[("acquired", "owner"), ("completed", None)],
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider."
            "tryvox_security.issue_stream_token",
            new_callable=AsyncMock,
            return_value="same-stream-token",
        ),
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        first, duplicate = await asyncio.gather(
            handle_tryvox_answer(
                _request("/api/v1/telephony/tryvox/answer", callback), 7, 13, 11
            ),
            handle_tryvox_answer(
                _request("/api/v1/telephony/tryvox/answer", callback), 7, 13, 11
            ),
        )

    first_payload = json.loads(first.body)
    duplicate_payload = json.loads(duplicate.body)
    assert duplicate_payload == first_payload
    assert duplicate_payload["instructions"][0]["url"].endswith(
        "?token=same-stream-token"
    )


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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("completed", None),
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
async def test_answer_route_forces_completion_when_finalize_claim_is_lost():
    """If the response was already generated but `finalize_callback` loses
    its claim (e.g. TTL expiry under slow processing), the route must not
    release the reservation for a retry to reprocess -- it should force the
    completion marker and still return the already-generated response."""
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
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.force_complete_callback",
            new_callable=AsyncMock,
        ) as force_complete,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_callback",
            new_callable=AsyncMock,
        ) as release_callback,
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

    force_complete.assert_awaited_once()
    release_callback.assert_not_awaited()
    payload = json.loads(response.body)
    assert payload["instructions"][0]["verb"] == "Stream"


@pytest.mark.asyncio
async def test_status_route_forces_completion_when_finalize_claim_is_lost():
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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.force_complete_callback",
            new_callable=AsyncMock,
        ) as force_complete,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_callback",
            new_callable=AsyncMock,
        ) as release_callback,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        result = await handle_tryvox_status(request, 13)

    assert result == {"status": "success"}
    process_status.assert_awaited_once()
    force_complete.assert_awaited_once()
    release_callback.assert_not_awaited()


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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("completed", None),
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        result = await handle_tryvox_status(request, 13)

    assert result == {"status": "success", "duplicate": True}
    process_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_status_callback_can_be_retried():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    body = {"CallUUID": "call-123", "Status": "hangup"}

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
            side_effect=[RuntimeError("transient"), None],
        ) as process_status,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            side_effect=[("acquired", "first-owner"), ("acquired", "retry-owner")],
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_callback",
            new_callable=AsyncMock,
            return_value=True,
        ) as release,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
            new_callable=AsyncMock,
            return_value=True,
        ) as finalize,
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)

        with pytest.raises(RuntimeError, match="transient"):
            await handle_tryvox_status(
                _request("/api/v1/telephony/tryvox/status/13", body), 13
            )
        result = await handle_tryvox_status(
            _request("/api/v1/telephony/tryvox/status/13", body), 13
        )

    assert result == {"status": "success"}
    assert process_status.await_count == 2
    assert release.await_args.args[-1] == "first-owner"
    assert finalize.await_args.args[-1] == "retry-owner"


@pytest.mark.asyncio
async def test_cancelled_status_callback_can_be_retried():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-123"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    body = {"CallUUID": "call-123", "Status": "ringing"}

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
            side_effect=[asyncio.CancelledError, None],
        ) as process_status,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            side_effect=[("acquired", "first-owner"), ("acquired", "retry-owner")],
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.release_callback",
            new_callable=AsyncMock,
            return_value=True,
        ) as release,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        db_client.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)

        with pytest.raises(asyncio.CancelledError):
            await handle_tryvox_status(
                _request("/api/v1/telephony/tryvox/status/13", body), 13
            )
        result = await handle_tryvox_status(
            _request("/api/v1/telephony/tryvox/status/13", body), 13
        )

    assert result == {"status": "success"}
    assert process_status.await_count == 2
    assert release.await_args.args[-1] == "first-owner"


@pytest.mark.asyncio
async def test_answer_route_claims_call_id_when_not_yet_persisted():
    """Outbound `initiate_call` can still be writing `call_id` when TryVox's
    signed Answer callback arrives. The first signed callback for a run
    should claim the call ID instead of being rejected."""
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={},
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
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.claim_call_id",
            new_callable=AsyncMock,
            return_value="call-123",
        ) as claim_call_id,
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.finalize_callback",
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
        db_client.update_workflow_run = AsyncMock()
        response = await handle_tryvox_answer(request, 7, 13, 11)

    claim_call_id.assert_awaited_once_with(13, "call-123")
    db_client.update_workflow_run.assert_awaited_once_with(
        run_id=13, gathered_context={"call_id": "call-123"}
    )
    payload = json.loads(response.body)
    assert payload["instructions"][0]["verb"] == "Stream"


@pytest.mark.asyncio
async def test_answer_route_rejects_conflicting_claimed_call_id():
    """If a different call already claimed this run's call ID, a later
    callback with a mismatched ID must still be rejected."""
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={},
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
            f"{ROUTES_MODULE}.tryvox_security.claim_call_id",
            new_callable=AsyncMock,
            return_value="call-123",
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
        ) as reserve_callback,
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        with pytest.raises(HTTPException) as exc:
            await handle_tryvox_answer(request, 7, 13, 11)

    assert exc.value.status_code == 403
    reserve_callback.assert_not_awaited()


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
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
            return_value=("acquired", "owner"),
        ) as reserve_callback,
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        with pytest.raises(HTTPException) as exc:
            await handle_tryvox_answer(request, 7, 13, 11)

    assert exc.value.status_code == 403
    reserve_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_route_rejects_missing_call_id_before_replay_claim():
    provider = _provider()
    workflow_run = SimpleNamespace(
        workflow_id=7,
        gathered_context={"call_id": "call-expected"},
        initial_context={"telephony_configuration_id": 5},
    )
    workflow = SimpleNamespace(id=7, organization_id=11)
    request = _request(
        "/api/v1/telephony/tryvox/answer",
        {"account_id": "TJaccount"},
    )

    with (
        patch(f"{ROUTES_MODULE}.db_client") as db_client,
        patch(
            f"{ROUTES_MODULE}.get_telephony_provider_for_run",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            f"{ROUTES_MODULE}.tryvox_security.reserve_callback",
            new_callable=AsyncMock,
        ) as reserve_callback,
    ):
        db_client.get_workflow_run = AsyncMock(return_value=workflow_run)
        db_client.get_workflow_by_id = AsyncMock(return_value=workflow)
        with pytest.raises(HTTPException) as exc:
            await handle_tryvox_answer(request, 7, 13, 11)

    assert exc.value.status_code == 403
    reserve_callback.assert_not_awaited()


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
