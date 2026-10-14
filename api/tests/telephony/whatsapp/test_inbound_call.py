"""Unit tests for the WhatsApp calling webhook and its WebRTC hand-off."""

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pipecat.transports.whatsapp.api import WhatsAppWebhookRequest
from starlette.requests import Request

from api.db import db_client
from api.services.telephony.providers.whatsapp import routes
from api.services.telephony.providers.whatsapp.routes import (
    _clients,
    _dispatch_terminate,
    _get_or_create_whatsapp_client,
    _owned_calls,
    _run_whatsapp_pipeline,
    _terminated_by_meta,
    handle_whatsapp_webhook,
)

ROUTES = "api.services.telephony.providers.whatsapp.routes"
CONCURRENCY = "api.services.call_concurrency.call_concurrency"


def _call(event: str, call_id: str, **overrides: Any) -> Dict[str, Any]:
    """One call event as Meta sends it; connect events also carry an SDP offer."""
    call: Dict[str, Any] = {
        "id": call_id,
        "from": "+15559876543",
        "to": "+15551234567",
        "event": event,
        "timestamp": "1725619200",
        "direction": "inbound",
    }
    if event == "connect":
        call["session"] = {"sdp_type": "offer", "sdp": "v=0\r\n"}
    call.update(overrides)
    return call


def _build_webhook_payload(
    calls: List[Dict[str, Any]],
    phone_number_id: str = "106540352242922",
    display_phone_number: str = "+15551234567",
    waba_id: str = "waba_123",
) -> Dict[str, Any]:
    value: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {
            "display_phone_number": display_phone_number,
            "phone_number_id": phone_number_id,
        },
        "calls": calls,
    }
    if any(call.get("event") == "connect" for call in calls):
        value["contacts"] = [{"profile": {"name": "Caller"}, "wa_id": "15559876543"}]
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba_id, "changes": [{"field": "calls", "value": value}]}],
    }


def _build_request(
    payload: Dict[str, Any],
    signature: Optional[str] = None,
    app_secret: Optional[str] = None,
) -> Request:
    """A Starlette request carrying the payload and, optionally, Meta's HMAC header."""
    body_bytes = json.dumps(payload).encode("utf-8")
    headers: List[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]

    if signature is not None:
        headers.append((b"x-hub-signature-256", signature.encode("utf-8")))
    elif app_secret is not None:
        sig = hmac.new(
            app_secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()
        headers.append((b"x-hub-signature-256", f"sha256={sig}".encode("utf-8")))

    async def mock_receive():
        return {"type": "http.request", "body": body_bytes}

    return Request(
        {"type": "http", "method": "POST", "headers": headers}, receive=mock_receive
    )


def _build_mock_config(
    app_secret: Optional[str] = "test_app_secret",
    access_token: str = "valid_token",
    org_id: int = 10,
    config_id: int = 1,
    waba_id: str = "waba_123",
) -> MagicMock:
    config = MagicMock()
    config.id = config_id
    config.organization_id = org_id
    config.credentials = {
        "waba_id": waba_id,
        "access_token": access_token,
        "app_secret": app_secret,
    }
    return config


def _build_mock_phone(
    phone_id: int = 2,
    address: str = "+15551234567",
    workflow_id: int = 99,
    is_active: bool = True,
) -> MagicMock:
    phone = MagicMock()
    phone.id = phone_id
    phone.address_normalized = address
    phone.is_active = is_active
    phone.inbound_workflow_id = workflow_id
    return phone


def _config_lookup(config: Optional[MagicMock]):
    return patch.object(
        db_client,
        "get_whatsapp_configuration_by_waba_id",
        AsyncMock(return_value=config),
    )


class TestWhatsAppConfigurationLookup(IsolatedAsyncioTestCase):
    async def test_ambiguous_business_account_routes_nowhere(self):
        """Two active configurations on one account: refuse rather than guess the tenant."""
        result = MagicMock()
        result.scalars.return_value.all.return_value = [
            MagicMock(id=10),
            MagicMock(id=20),
        ]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = session

        with patch.object(db_client, "async_session", return_value=session_ctx):
            config = await db_client.get_whatsapp_configuration_by_waba_id("12345")

        self.assertIsNone(config)


class TestWhatsAppInboundConnect(IsolatedAsyncioTestCase):
    def setUp(self):
        _clients.clear()
        _owned_calls.clear()
        _terminated_by_meta.clear()
        # No Redis here: the connect claim is granted and the database
        # duplicate check is the guard, as in a deployment without Redis.
        self.enterContext(patch(f"{ROUTES}._get_redis", AsyncMock(return_value=None)))

    async def test_connect_creates_workflow_run_and_answers_through_the_client(self):
        phone_number_id = "106540352242922"
        call_id = "call_abc123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            [_call("connect", call_id)], phone_number_id=phone_number_id
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(app_secret=app_secret)

        mock_workflow = MagicMock(id=99, user_id=5, organization_id=10)
        mock_workflow_run = MagicMock(id=101)

        mock_client = AsyncMock()

        async def fake_handle_webhook_request(request, connection_callback, **kwargs):
            await connection_callback(MagicMock(), MagicMock(id=call_id))

        mock_client.handle_webhook_request = AsyncMock(
            side_effect=fake_handle_webhook_request
        )

        with (
            _config_lookup(mock_config),
            patch.object(
                db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client,
                "list_phone_numbers_for_config",
                AsyncMock(return_value=[_build_mock_phone()]),
            ),
            patch.object(
                db_client, "get_workflow", AsyncMock(return_value=mock_workflow)
            ),
            patch.object(
                db_client,
                "create_workflow_run",
                AsyncMock(return_value=mock_workflow_run),
            ),
            patch(f"{CONCURRENCY}.acquire_org_slot", AsyncMock(return_value="slot1")),
            patch(f"{CONCURRENCY}.bind_workflow_run", AsyncMock()),
            patch(
                f"{ROUTES}.prepare_workflow_run_inputs",
                AsyncMock(return_value=MagicMock(definition_id=1)),
            ),
            patch(
                f"{ROUTES}.authorize_workflow_run_start",
                AsyncMock(return_value=MagicMock(has_quota=True)),
            ),
            patch(f"{ROUTES}._get_or_create_whatsapp_client", return_value=mock_client),
            patch(f"{ROUTES}._ensure_redis_subscriber"),
            patch(f"{ROUTES}._run_whatsapp_pipeline", AsyncMock()) as run_pipeline,
        ):
            response = await handle_whatsapp_webhook(request)

            self.assertEqual(response, {"status": "success"})
            mock_client.handle_webhook_request.assert_awaited_once()
            request_arg = mock_client.handle_webhook_request.await_args.kwargs[
                "request"
            ]
            self.assertIsInstance(request_arg, WhatsAppWebhookRequest)
            self.assertIn(call_id, _owned_calls)
            run_pipeline.assert_called_once()
            self.assertEqual(run_pipeline.call_args.kwargs["call_id"], call_id)
            self.assertEqual(run_pipeline.call_args.kwargs["workflow_run_id"], 101)
            self.assertEqual(
                run_pipeline.call_args.kwargs["access_token"], "valid_token"
            )

    async def test_connect_rejects_a_number_not_on_the_configuration(self):
        phone_number_id = "106540352242922"
        call_id = "call_unmatched"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            [_call("connect", call_id, to="+15559999999")],
            phone_number_id=phone_number_id,
            display_phone_number="+15559999999",
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(app_secret=app_secret)

        with (
            _config_lookup(mock_config),
            patch.object(
                db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client,
                "list_phone_numbers_for_config",
                AsyncMock(return_value=[_build_mock_phone()]),
            ),
            patch(f"{ROUTES}._reject_whatsapp_call", AsyncMock()) as mock_reject,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_reject.assert_awaited_once_with(phone_number_id, call_id, "valid_token")

    async def test_connect_ignores_a_call_that_already_has_a_run(self):
        """Meta retries a connect it thinks went unanswered; the run must not double."""
        phone_number_id = "106540352242922"
        call_id = "call_dup_123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            [_call("connect", call_id)], phone_number_id=phone_number_id
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(app_secret=app_secret)

        with (
            _config_lookup(mock_config),
            patch.object(
                db_client,
                "get_workflow_run_by_call_id",
                AsyncMock(return_value=MagicMock(id=101)),
            ),
            patch(f"{CONCURRENCY}.acquire_org_slot", AsyncMock()) as mock_acquire,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_acquire.assert_not_called()

    async def test_connect_lost_to_another_worker_is_ignored(self):
        """Two deliveries of one connect overlap; only the claim winner answers."""
        phone_number_id = "106540352242922"
        app_secret = "test_app_secret"
        payload = _build_webhook_payload(
            [_call("connect", "call_raced")], phone_number_id=phone_number_id
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=None)  # NX: another worker holds it

        with (
            _config_lookup(_build_mock_config(app_secret=app_secret)),
            patch(f"{ROUTES}._get_redis", AsyncMock(return_value=mock_redis)),
            patch.object(
                db_client, "get_workflow_run_by_call_id", AsyncMock()
            ) as dup_check,
            patch.object(db_client, "create_workflow_run", AsyncMock()) as create_run,
            patch(f"{CONCURRENCY}.acquire_org_slot", AsyncMock()) as mock_acquire,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_redis.set.assert_awaited_once()
        self.assertEqual(
            mock_redis.set.await_args.args[0], "whatsapp:call:call_raced:claim"
        )
        self.assertTrue(mock_redis.set.await_args.kwargs["nx"])
        dup_check.assert_not_called()
        create_run.assert_not_called()
        mock_acquire.assert_not_called()

    async def test_connect_failing_before_an_answer_releases_the_claim(self):
        """An unexpected error leaves the call untouched; Meta's retry must get through."""
        phone_number_id = "106540352242922"
        app_secret = "test_app_secret"
        payload = _build_webhook_payload(
            [_call("connect", "call_db_down")], phone_number_id=phone_number_id
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)

        with (
            _config_lookup(_build_mock_config(app_secret=app_secret)),
            patch(f"{ROUTES}._get_redis", AsyncMock(return_value=mock_redis)),
            patch.object(
                db_client,
                "get_workflow_run_by_call_id",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 500)
        mock_redis.eval.assert_awaited_once()
        script, nkeys, key, owner = mock_redis.eval.await_args.args
        self.assertEqual(
            (nkeys, key, owner),
            (1, "whatsapp:call:call_db_down:claim", routes._WORKER_ID),
        )
        self.assertIn("del", script)

    async def test_batched_connects_are_answered_one_call_at_a_time(self):
        """Pipecat's client stops at the first call it sees, so each gets its own payload."""
        phone_number_id = "106540352242922"
        app_secret = "test_app_secret"
        payload = _build_webhook_payload(
            [_call("connect", "call_a"), _call("connect", "call_b")],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(app_secret=app_secret)
        mock_workflow = MagicMock(id=99, user_id=5, organization_id=10)

        handed_over: List[List[str]] = []

        async def fake_handle_webhook_request(request, connection_callback, **kwargs):
            calls = request.entry[0].changes[0].value.calls
            handed_over.append([c.id for c in calls])
            await connection_callback(MagicMock(), MagicMock(id=calls[0].id))

        mock_client = AsyncMock()
        mock_client.handle_webhook_request = AsyncMock(
            side_effect=fake_handle_webhook_request
        )

        with (
            _config_lookup(mock_config),
            patch.object(
                db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client,
                "list_phone_numbers_for_config",
                AsyncMock(return_value=[_build_mock_phone()]),
            ),
            patch.object(
                db_client, "get_workflow", AsyncMock(return_value=mock_workflow)
            ),
            patch.object(
                db_client,
                "create_workflow_run",
                AsyncMock(side_effect=[MagicMock(id=101), MagicMock(id=102)]),
            ),
            patch(f"{CONCURRENCY}.acquire_org_slot", AsyncMock(return_value="slot1")),
            patch(f"{CONCURRENCY}.bind_workflow_run", AsyncMock()),
            patch(
                f"{ROUTES}.prepare_workflow_run_inputs",
                AsyncMock(return_value=MagicMock(definition_id=1)),
            ),
            patch(
                f"{ROUTES}.authorize_workflow_run_start",
                AsyncMock(return_value=MagicMock(has_quota=True)),
            ),
            patch(f"{ROUTES}._get_or_create_whatsapp_client", return_value=mock_client),
            patch(f"{ROUTES}._ensure_redis_subscriber"),
            patch(f"{ROUTES}._run_whatsapp_pipeline", AsyncMock()) as run_pipeline,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        self.assertEqual(handed_over, [["call_a"], ["call_b"]])
        self.assertEqual(
            [c.kwargs["call_id"] for c in run_pipeline.call_args_list],
            ["call_a", "call_b"],
        )
        self.assertEqual(
            [c.kwargs["workflow_run_id"] for c in run_pipeline.call_args_list],
            [101, 102],
        )

    async def test_connect_releases_the_slot_when_run_creation_fails(self):
        phone_number_id = "106540352242922"
        call_id = "call_fail_init"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            [_call("connect", call_id)], phone_number_id=phone_number_id
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(app_secret=app_secret)

        with (
            _config_lookup(mock_config),
            patch.object(
                db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client,
                "list_phone_numbers_for_config",
                AsyncMock(return_value=[_build_mock_phone()]),
            ),
            patch.object(
                db_client,
                "get_workflow",
                AsyncMock(return_value=MagicMock(id=99, organization_id=10)),
            ),
            patch(f"{CONCURRENCY}.acquire_org_slot", AsyncMock(return_value="slot1")),
            patch(
                f"{ROUTES}.prepare_workflow_run_inputs",
                AsyncMock(side_effect=RuntimeError("inputs failed")),
            ),
            patch(f"{CONCURRENCY}.release_slot", AsyncMock()) as mock_release_slot,
            patch(f"{ROUTES}._reject_whatsapp_call", AsyncMock()) as mock_reject,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_release_slot.assert_awaited_once_with("slot1")
        mock_reject.assert_awaited_once_with(phone_number_id, call_id, "valid_token")

    async def test_connect_rejects_invalid_signature(self):
        payload = _build_webhook_payload([_call("connect", "c1")])
        request = _build_request(payload, signature="sha256=wrong_signature")

        with _config_lookup(_build_mock_config(app_secret="secret123")):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Invalid webhook signature")

    async def test_connect_rejects_missing_signature(self):
        payload = _build_webhook_payload([_call("connect", "c1")])
        request = _build_request(payload)

        with _config_lookup(_build_mock_config(app_secret="secret123")):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Missing webhook signature")

    async def test_calling_events_need_a_configured_app_secret(self):
        payload = _build_webhook_payload([_call("connect", "c1")])
        request = _build_request(payload, signature="sha256=some_sig")

        with _config_lookup(_build_mock_config(app_secret=None)):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "Webhook signature verification failed: app_secret not configured",
        )

    async def test_unknown_business_account_is_not_served(self):
        payload = _build_webhook_payload([_call("connect", "c1")])
        request = _build_request(payload, app_secret="secret123")

        with _config_lookup(None) as lookup:
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 404)
        lookup.assert_awaited_once_with("waba_123")

    async def test_entry_without_a_business_account_is_rejected(self):
        payload = _build_webhook_payload([_call("connect", "c1")], waba_id="")
        request = _build_request(payload, app_secret="secret123")

        with _config_lookup(_build_mock_config()) as lookup:
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 400)
        lookup.assert_not_awaited()


class TestWhatsAppClientCache(IsolatedAsyncioTestCase):
    def setUp(self):
        _clients.clear()

    def test_cached_client_gets_fresh_ice_servers(self):
        """TURN credentials expire; a client reused across calls must not keep the old ones."""
        cached = MagicMock()
        _clients["123"] = cached
        fresh = [MagicMock()]

        with patch(f"{ROUTES}._build_whatsapp_ice_servers", return_value=fresh):
            client = _get_or_create_whatsapp_client(
                phone_number_id="123", access_token="tok", app_secret=None
            )

        self.assertIs(client, cached)
        cached.update_whatsapp_token.assert_called_once_with("tok")
        cached.update_ice_servers.assert_called_once_with(fresh)


class TestWhatsAppTerminate(IsolatedAsyncioTestCase):
    def setUp(self):
        _clients.clear()
        _owned_calls.clear()
        _terminated_by_meta.clear()

    async def test_terminate_is_handed_to_the_client_holding_the_call(self):
        """Pipecat's client closes the peer connection; the pipeline completes the run."""
        call_id = "call_to_terminate"
        phone_number_id = "123"
        app_secret = "test_app_secret"
        mock_client = AsyncMock()
        _clients[phone_number_id] = mock_client
        _owned_calls.add(call_id)

        payload = _build_webhook_payload(
            [_call("terminate", call_id, status="COMPLETED", duration=42)],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)

        with (
            _config_lookup(_build_mock_config(app_secret=app_secret)),
            patch.object(db_client, "update_workflow_run", AsyncMock()) as mock_update,
            patch(f"{ROUTES}._get_redis", AsyncMock(return_value=None)),
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_client.handle_webhook_request.assert_awaited_once()
        request_arg = mock_client.handle_webhook_request.await_args.args[0]
        self.assertIsInstance(request_arg, WhatsAppWebhookRequest)
        self.assertEqual(request_arg.entry[0].changes[0].value.calls[0].id, call_id)
        self.assertIn(call_id, _terminated_by_meta)
        mock_update.assert_not_called()

    async def test_terminate_for_a_call_held_elsewhere_is_broadcast(self):
        """No client for the number here: another worker answered it, so tell them."""
        call_id = "call_cross_worker"
        phone_number_id = "123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            [_call("terminate", call_id, status="COMPLETED")],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_redis = AsyncMock()

        with (
            _config_lookup(_build_mock_config(app_secret=app_secret)),
            patch(f"{ROUTES}._get_redis", AsyncMock(return_value=mock_redis)),
            patch.object(db_client, "update_workflow_run", AsyncMock()) as mock_update,
        ):
            response = await handle_whatsapp_webhook(request)

        self.assertEqual(response, {"status": "success"})
        mock_redis.publish.assert_awaited_once()
        channel, message = mock_redis.publish.await_args.args
        self.assertEqual(channel, routes.REDIS_TERMINATE_CHANNEL)
        data = json.loads(message)
        self.assertEqual(data["worker_id"], routes._WORKER_ID)
        self.assertEqual(data["phone_number_id"], phone_number_id)
        self.assertEqual(data["call_id"], call_id)
        self.assertEqual(data["payload"], payload)
        mock_update.assert_not_called()
        self.assertNotIn(call_id, _terminated_by_meta)

    async def test_broadcast_terminate_reaches_the_owning_client(self):
        call_id = "call_remote"
        phone_number_id = "123"
        mock_client = AsyncMock()
        _clients[phone_number_id] = mock_client
        _owned_calls.add(call_id)
        payload = _build_webhook_payload(
            [_call("terminate", call_id, status="COMPLETED")],
            phone_number_id=phone_number_id,
        )

        await _dispatch_terminate(phone_number_id, payload, call_id)

        mock_client.handle_webhook_request.assert_awaited_once()
        self.assertIn(call_id, _terminated_by_meta)

    async def test_batched_terminates_close_each_call(self):
        """The client stops at the first terminate it sees, so each call gets its own."""
        phone_number_id = "123"
        app_secret = "test_app_secret"
        mock_client = AsyncMock()
        _clients[phone_number_id] = mock_client
        _owned_calls.update({"call_a", "call_b"})

        payload = _build_webhook_payload(
            [
                _call("terminate", "call_a", status="COMPLETED"),
                _call("terminate", "call_b", status="COMPLETED"),
            ],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)

        with (
            _config_lookup(_build_mock_config(app_secret=app_secret)),
            patch(f"{ROUTES}._get_redis", AsyncMock(return_value=None)),
        ):
            await handle_whatsapp_webhook(request)

        handed_over = [
            [c.id for c in call.args[0].entry[0].changes[0].value.calls]
            for call in mock_client.handle_webhook_request.await_args_list
        ]
        self.assertEqual(handed_over, [["call_a"], ["call_b"]])
        self.assertEqual(_terminated_by_meta, {"call_a", "call_b"})

    async def test_terminate_rejects_missing_signature(self):
        call_id = "call_to_terminate"
        phone_number_id = "123"
        mock_client = AsyncMock()
        _clients[phone_number_id] = mock_client

        payload = _build_webhook_payload(
            [_call("terminate", call_id)], phone_number_id=phone_number_id
        )
        request = _build_request(payload)

        with _config_lookup(_build_mock_config()):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 403)
        mock_client.handle_webhook_request.assert_not_called()

    async def test_terminate_rejects_invalid_signature(self):
        call_id = "call_to_terminate"
        phone_number_id = "123"
        mock_client = AsyncMock()
        _clients[phone_number_id] = mock_client

        payload = _build_webhook_payload(
            [_call("terminate", call_id)], phone_number_id=phone_number_id
        )
        request = _build_request(payload, signature="sha256=forged_signature_digest")

        with _config_lookup(_build_mock_config()):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

        self.assertEqual(ctx.exception.status_code, 403)
        mock_client.handle_webhook_request.assert_not_called()


class TestWhatsAppPipelineTeardown(IsolatedAsyncioTestCase):
    def setUp(self):
        _owned_calls.clear()
        _terminated_by_meta.clear()

    async def _run(self, call_id: str):
        with (
            patch(f"{ROUTES}.run_pipeline_smallwebrtc", AsyncMock()),
            patch(f"{ROUTES}._terminate_whatsapp_call", AsyncMock()) as terminate,
            patch(f"{CONCURRENCY}.release_workflow_run_slot", AsyncMock()) as release,
        ):
            await _run_whatsapp_pipeline(
                connection=MagicMock(),
                workflow_id=99,
                workflow_run_id=101,
                user_id=5,
                organization_id=10,
                call_id=call_id,
                phone_number_id="123",
                access_token="valid_token",
            )
        return terminate, release

    async def test_agent_hanging_up_ends_the_call_on_meta(self):
        _owned_calls.add("call_agent_hangup")

        terminate, release = await self._run("call_agent_hangup")

        terminate.assert_awaited_once_with("123", "call_agent_hangup", "valid_token")
        release.assert_awaited_once_with(101)
        self.assertNotIn("call_agent_hangup", _owned_calls)

    async def test_call_meta_already_ended_is_not_ended_again(self):
        _owned_calls.add("call_caller_hangup")
        _terminated_by_meta.add("call_caller_hangup")

        terminate, release = await self._run("call_caller_hangup")

        terminate.assert_not_called()
        release.assert_awaited_once_with(101)
        self.assertNotIn("call_caller_hangup", _terminated_by_meta)

    async def test_teardown_survives_a_failed_run_lookup(self):
        """A database error while marking the run must still end the call and free the slot."""
        _owned_calls.add("call_db_down")

        with (
            patch(
                f"{ROUTES}.run_pipeline_smallwebrtc",
                AsyncMock(side_effect=RuntimeError("pipeline died")),
            ),
            patch.object(
                db_client,
                "get_workflow_run",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(f"{ROUTES}.mark_workflow_run_failed", AsyncMock()) as mark_failed,
            patch(f"{ROUTES}._terminate_whatsapp_call", AsyncMock()) as terminate,
            patch(f"{CONCURRENCY}.release_workflow_run_slot", AsyncMock()) as release,
        ):
            await _run_whatsapp_pipeline(
                connection=MagicMock(),
                workflow_id=99,
                workflow_run_id=101,
                user_id=5,
                organization_id=10,
                call_id="call_db_down",
                phone_number_id="123",
                access_token="valid_token",
            )

        mark_failed.assert_not_called()
        terminate.assert_awaited_once_with("123", "call_db_down", "valid_token")
        release.assert_awaited_once_with(101)
