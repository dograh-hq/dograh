"""Unit tests for WhatsApp inbound calling webhook and WebRTC integration."""

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from starlette.requests import Request

from api.db import db_client
from api.services.telephony.providers.whatsapp.routes import (
    _active_connections,
    handle_webhook_verification,
    handle_whatsapp_webhook,
)


def _build_webhook_payload(
    calls: List[Dict[str, Any]],
    phone_number_id: str = "106540352242922",
    display_phone_number: str = "+15551234567",
) -> Dict[str, Any]:
    """Helper to construct a Meta WhatsApp Business webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_123",
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": display_phone_number,
                                "phone_number_id": phone_number_id,
                            },
                            "calls": calls,
                        },
                    }
                ],
            }
        ],
    }


def _build_request(
    payload: Dict[str, Any],
    signature: Optional[str] = None,
    app_secret: Optional[str] = None,
    custom_headers: Optional[Dict[str, str]] = None,
) -> Request:
    """Helper to construct a Starlette Request with appropriate HMAC headers."""
    body_bytes = json.dumps(payload).encode("utf-8")
    headers: List[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]

    if signature is not None:
        headers.append((b"x-hub-signature-256", signature.encode("utf-8")))
    elif app_secret is not None:
        sig = hmac.new(
            app_secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()
        headers.append((b"x-hub-signature-256", f"sha256={sig}".encode("utf-8")))

    if custom_headers:
        for k, v in custom_headers.items():
            headers.append((k.encode("utf-8"), v.encode("utf-8")))

    async def mock_receive():
        return {"type": "http.request", "body": body_bytes}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": headers,
    }
    return Request(scope, receive=mock_receive)


def _build_mock_config(
    phone_number_id: str = "106540352242922",
    app_secret: Optional[str] = "test_app_secret",
    access_token: str = "valid_token",
    org_id: int = 10,
    config_id: int = 1,
) -> MagicMock:
    """Helper to construct a mock TelephonyConfigurationModel."""
    config = MagicMock()
    config.id = config_id
    config.organization_id = org_id
    config.credentials = {
        "phone_number_id": phone_number_id,
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
    """Helper to construct a mock TelephonyPhoneNumberModel."""
    phone = MagicMock()
    phone.id = phone_id
    phone.address_normalized = address
    phone.is_active = is_active
    phone.inbound_workflow_id = workflow_id
    return phone


class TestWhatsAppInboundCalling(IsolatedAsyncioTestCase):
    def setUp(self):
        _active_connections.clear()

    async def test_webhook_verification_via_db_fallback(self):
        """Verify GET /webhook falls back to DB telephony configuration if env var not set."""
        mock_config = MagicMock()
        mock_config.id = 42

        with patch(
            "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            None,
        ), patch.object(
            db_client,
            "get_whatsapp_configuration_by_verify_token",
            AsyncMock(return_value=mock_config),
        ):
            response = await handle_webhook_verification(
                hub_mode="subscribe",
                hub_verify_token="custom_org_token",
                hub_challenge="challenge_777",
            )

            self.assertIsInstance(response, PlainTextResponse)
            self.assertEqual(response.body, b"challenge_777")

    async def test_webhook_connect_creates_workflow_run_and_answers_call(self):
        """Verify POST /webhook handles connect event, creates workflow_run, and answers call."""
        phone_number_id = "106540352242922"
        call_id = "call_abc123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[
                {
                    "id": call_id,
                    "from": "+15559876543",
                    "to": "+15551234567",
                    "event": "connect",
                    "timestamp": "1725619200",
                    "direction": "inbound",
                    "session": {
                        "sdp_type": "offer",
                        "sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 50000 UDP/TLS/RTP/SAVPF 111\r\n",
                    },
                }
            ],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)
        mock_phone = _build_mock_phone()

        mock_workflow = MagicMock()
        mock_workflow.id = 99
        mock_workflow.user_id = 5
        mock_workflow.organization_id = 10

        mock_workflow_run = MagicMock()
        mock_workflow_run.id = 101

        mock_client = AsyncMock()

        async def fake_handle_webhook_request(request, connection_callback, **kwargs):
            mock_connection = MagicMock()
            mock_call = MagicMock()
            mock_call.id = call_id
            await connection_callback(mock_connection, mock_call)

        mock_client.handle_webhook_request = AsyncMock(side_effect=fake_handle_webhook_request)

        with patch.object(db_client, "get_whatsapp_configuration_by_phone_number_id", AsyncMock(return_value=mock_config)), \
             patch.object(db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)), \
             patch.object(db_client, "list_phone_numbers_for_config", AsyncMock(return_value=[mock_phone])), \
             patch.object(db_client, "get_workflow", AsyncMock(return_value=mock_workflow)), \
             patch.object(db_client, "create_workflow_run", AsyncMock(return_value=mock_workflow_run)), \
             patch("api.services.call_concurrency.call_concurrency.acquire_org_slot", AsyncMock(return_value="slot1")), \
             patch("api.services.call_concurrency.call_concurrency.bind_workflow_run", AsyncMock()), \
             patch("api.services.telephony.providers.whatsapp.routes.prepare_workflow_run_inputs", AsyncMock(return_value=MagicMock(definition_id=1))), \
             patch("api.services.telephony.providers.whatsapp.routes.authorize_workflow_run_start", AsyncMock(return_value=MagicMock(has_quota=True))), \
             patch("api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client", return_value=mock_client), \
             patch("api.services.telephony.providers.whatsapp.routes._get_redis", AsyncMock(return_value=None)), \
             patch("api.services.telephony.providers.whatsapp.routes._run_whatsapp_pipeline", AsyncMock()):

            response = await handle_whatsapp_webhook(request)

            self.assertEqual(response, {"status": "success"})
            mock_client.handle_webhook_request.assert_called_once()
            self.assertIn(call_id, _active_connections)
            self.assertEqual(_active_connections[call_id][1], mock_workflow_run.id)
            self.assertEqual(_active_connections[call_id][2], mock_workflow.organization_id)

    async def test_webhook_connect_rejects_unmatched_destination(self):
        """Verify POST /webhook rejects call when destination does not match active configured numbers."""
        phone_number_id = "106540352242922"
        call_id = "call_unmatched"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[
                {
                    "id": call_id,
                    "from": "+15559876543",
                    "to": "+15559999999",
                    "event": "connect",
                    "session": {"sdp_type": "offer"},
                }
            ],
            phone_number_id=phone_number_id,
            display_phone_number="+15559999999",
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)
        mock_phone = _build_mock_phone()

        with patch.object(db_client, "get_whatsapp_configuration_by_phone_number_id", AsyncMock(return_value=mock_config)), \
             patch.object(db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)), \
             patch.object(db_client, "list_phone_numbers_for_config", AsyncMock(return_value=[mock_phone])), \
             patch("api.services.telephony.providers.whatsapp.routes._reject_whatsapp_call", AsyncMock()) as mock_reject:

            response = await handle_whatsapp_webhook(request)
            self.assertEqual(response, {"status": "success"})
            mock_reject.assert_awaited_once_with(phone_number_id, call_id, "valid_token")

    async def test_webhook_connect_idempotent_on_duplicate_call_id(self):
        """Verify POST /webhook ignores retried connect event if call is already registered."""
        phone_number_id = "106540352242922"
        call_id = "call_dup_123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[
                {
                    "id": call_id,
                    "event": "connect",
                    "session": {"sdp_type": "offer"},
                }
            ],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)

        # Put call_id in active connections
        _active_connections[call_id] = (AsyncMock(), 101, 10, phone_number_id)

        with patch.object(db_client, "get_whatsapp_configuration_by_phone_number_id", AsyncMock(return_value=mock_config)), \
             patch("api.services.call_concurrency.call_concurrency.acquire_org_slot", AsyncMock()) as mock_acquire:

            response = await handle_whatsapp_webhook(request)
            self.assertEqual(response, {"status": "success"})
            mock_acquire.assert_not_called()

    async def test_webhook_connect_releases_slot_on_run_creation_failure(self):
        """Verify concurrency slot is cleanly released if workflow run creation fails."""
        phone_number_id = "106540352242922"
        call_id = "call_fail_init"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[
                {
                    "id": call_id,
                    "from": "+15559876543",
                    "to": "+15551234567",
                    "event": "connect",
                    "session": {"sdp_type": "offer"},
                }
            ],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)
        mock_phone = _build_mock_phone()

        mock_workflow = MagicMock()
        mock_workflow.id = 99
        mock_workflow.organization_id = 10

        with patch.object(db_client, "get_whatsapp_configuration_by_phone_number_id", AsyncMock(return_value=mock_config)), \
             patch.object(db_client, "get_workflow_run_by_call_id", AsyncMock(return_value=None)), \
             patch.object(db_client, "list_phone_numbers_for_config", AsyncMock(return_value=[mock_phone])), \
             patch.object(db_client, "get_workflow", AsyncMock(return_value=mock_workflow)), \
             patch("api.services.call_concurrency.call_concurrency.acquire_org_slot", AsyncMock(return_value="slot1")), \
             patch("api.services.telephony.providers.whatsapp.routes.prepare_workflow_run_inputs", AsyncMock(side_effect=RuntimeError("inputs failed"))), \
             patch("api.services.call_concurrency.call_concurrency.release_slot", AsyncMock()) as mock_release_slot, \
             patch("api.services.telephony.providers.whatsapp.routes._reject_whatsapp_call", AsyncMock()) as mock_reject:

            response = await handle_whatsapp_webhook(request)
            self.assertEqual(response, {"status": "success"})
            mock_release_slot.assert_awaited_once_with("slot1")
            mock_reject.assert_awaited_once_with(phone_number_id, call_id, "valid_token")

    async def test_webhook_connect_rejects_invalid_signature(self):
        """Verify POST /webhook raises 403 when signature does not match app_secret."""
        phone_number_id = "106540352242922"
        app_secret = "secret123"

        payload = _build_webhook_payload(
            calls=[{"id": "c1", "event": "connect", "session": {"sdp_type": "offer"}}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, signature="sha256=wrong_signature")
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Invalid webhook signature")

    async def test_webhook_connect_rejects_missing_signature(self):
        """Verify POST /webhook raises 403 when x-hub-signature-256 is omitted on connect."""
        phone_number_id = "106540352242922"
        app_secret = "secret123"

        payload = _build_webhook_payload(
            calls=[{"id": "c1", "event": "connect", "session": {"sdp_type": "offer"}}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload)  # No signature or secret provided
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Missing webhook signature")

    async def test_webhook_calling_rejects_missing_app_secret(self):
        """Verify POST /webhook raises 403 when app_secret is not configured."""
        phone_number_id = "106540352242922"

        payload = _build_webhook_payload(
            calls=[{"id": "c1", "event": "connect", "session": {"sdp_type": "offer"}}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, signature="sha256=some_sig")
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=None)

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(
                ctx.exception.detail,
                "Webhook signature verification failed: app_secret not configured",
            )

    async def test_webhook_terminate_disconnects_peer_connection(self):
        """Verify POST /webhook with valid signature handles terminate event and disconnects."""
        call_id = "call_to_terminate"
        phone_number_id = "123"
        app_secret = "test_app_secret"
        mock_connection = AsyncMock()
        _active_connections[call_id] = (mock_connection, 101, 10, phone_number_id)

        payload = _build_webhook_payload(
            calls=[{"id": call_id, "event": "terminate"}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ), patch.object(
            db_client, "update_workflow_run", AsyncMock()
        ), patch(
            "api.services.call_concurrency.call_concurrency.release_workflow_run_slot",
            AsyncMock(),
        ), patch(
            "api.services.telephony.providers.whatsapp.routes._get_redis",
            AsyncMock(return_value=None),
        ):
            response = await handle_whatsapp_webhook(request)

            self.assertEqual(response, {"status": "success"})
            mock_connection.disconnect.assert_called_once()
            self.assertNotIn(call_id, _active_connections)

    async def test_webhook_terminate_cross_worker_cleanup(self):
        """Verify terminate event for call on another worker cleans up workflow_run and releases slot."""
        call_id = "call_cross_worker"
        phone_number_id = "123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[{"id": call_id, "event": "terminate"}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)

        mock_run = MagicMock()
        mock_run.id = 555
        mock_run.is_completed = False

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ), patch.object(
            db_client,
            "get_workflow_run_by_call_id",
            AsyncMock(return_value=mock_run),
        ), patch.object(
            db_client, "update_workflow_run", AsyncMock()
        ) as mock_update, patch(
            "api.services.call_concurrency.call_concurrency.release_workflow_run_slot",
            AsyncMock(),
        ) as mock_release, patch(
            "api.services.telephony.providers.whatsapp.routes._get_redis",
            AsyncMock(return_value=None),
        ):
            response = await handle_whatsapp_webhook(request)

            self.assertEqual(response, {"status": "success"})
            mock_update.assert_awaited_once_with(
                555, is_completed=True, state="completed"
            )
            mock_release.assert_awaited_once_with(555)

    async def test_webhook_terminate_rejects_missing_signature(self):
        """Verify POST /webhook raises 403 and does NOT disconnect when signature is omitted on terminate."""
        call_id = "call_to_terminate"
        phone_number_id = "123"
        mock_connection = AsyncMock()
        _active_connections[call_id] = (mock_connection, 101, 10, phone_number_id)

        payload = _build_webhook_payload(
            calls=[{"id": call_id, "event": "terminate"}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload)  # No signature or secret
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret="test_app_secret")

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Missing webhook signature")
            mock_connection.disconnect.assert_not_called()
            self.assertIn(call_id, _active_connections)

    async def test_webhook_terminate_rejects_invalid_signature(self):
        """Verify POST /webhook raises 403 and does NOT disconnect when signature is forged on terminate."""
        call_id = "call_to_terminate"
        phone_number_id = "123"
        mock_connection = AsyncMock()
        _active_connections[call_id] = (mock_connection, 101, 10, phone_number_id)

        payload = _build_webhook_payload(
            calls=[{"id": call_id, "event": "terminate"}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, signature="sha256=forged_signature_digest")
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret="test_app_secret")

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_whatsapp_webhook(request)

            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "Invalid webhook signature")
            mock_connection.disconnect.assert_not_called()
            self.assertIn(call_id, _active_connections)

    async def test_webhook_terminate_publishes_to_redis(self):
        """Verify POST /webhook publishes terminate event to Redis pub/sub."""
        call_id = "call_redis_pub"
        phone_number_id = "123"
        app_secret = "test_app_secret"

        payload = _build_webhook_payload(
            calls=[{"id": call_id, "event": "terminate"}],
            phone_number_id=phone_number_id,
        )
        request = _build_request(payload, app_secret=app_secret)
        mock_config = _build_mock_config(phone_number_id=phone_number_id, app_secret=app_secret)
        mock_redis = AsyncMock()

        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_phone_number_id",
            AsyncMock(return_value=mock_config),
        ), patch(
            "api.services.telephony.providers.whatsapp.routes._get_redis",
            AsyncMock(return_value=mock_redis),
        ), patch.object(
            db_client,
            "get_workflow_run_by_call_id",
            AsyncMock(return_value=None),
        ):
            response = await handle_whatsapp_webhook(request)
            self.assertEqual(response, {"status": "success"})
            mock_redis.publish.assert_awaited_once()
            mock_redis.delete.assert_awaited_once_with(f"whatsapp:call:{call_id}")

