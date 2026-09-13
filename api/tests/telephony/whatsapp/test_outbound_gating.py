"""Holistic regression and integration test cases for WhatsApp Outbound Calling.

Tests:
1. Gating of audio/greeting in Pipecat pipeline until call is answered (status: ACCEPTED).
2. Decoupling of WebRTC SDP answer from call acceptance.
3. Live Meta call permission synchronization and revocation handling.
4. Telephony status reporting (ringing vs connected).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.db import db_client
from api.services.pipecat.call_gate import ANSWERED, OutboundCallGate


class TestWhatsAppOutboundAnswerGating(IsolatedAsyncioTestCase):
    """Test suite for answer gating in pipeline event handlers."""

    async def test_on_client_connected_waits_for_call_answered_event(self):
        """Verify on_client_connected does NOT start recording or speaking until call_answered_event is set."""
        from api.services.pipecat.event_handlers import register_event_handlers

        registered_handlers = {}

        def mock_event_handler(event_name):
            def decorator(fn):
                registered_handlers[event_name] = fn
                return fn

            return decorator

        mock_transport = MagicMock()
        mock_transport.event_handler.side_effect = mock_event_handler

        mock_task = MagicMock()
        mock_task.event_handler.side_effect = mock_event_handler
        mock_task.queue_frames = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.is_call_disposed.return_value = False
        mock_engine.set_node = AsyncMock()
        mock_engine.queue_node_opening = AsyncMock()
        mock_engine.workflow.start_node_id = "start_node"

        mock_audio_buffer = MagicMock()
        mock_audio_buffer.start_recording = AsyncMock()

        call_answered_event = OutboundCallGate()

        with patch("api.services.pipecat.event_handlers.capture_event"):
            register_event_handlers(
                task=mock_task,
                transport=mock_transport,
                workflow_run_id=101,
                engine=mock_engine,
                audio_buffer=mock_audio_buffer,
                in_memory_logs_buffer=MagicMock(),
                transcript_log_coordinator=MagicMock(),
                pipeline_metrics_aggregator=MagicMock(),
                termination_funnel=MagicMock(),
                call_answered_event=call_answered_event,
            )

            # Trigger on_pipeline_started handler so ready_state["pipeline_started"] = True
            pipeline_started_fn = registered_handlers.get("on_pipeline_started")
            if pipeline_started_fn:
                await pipeline_started_fn(mock_task, MagicMock())

            on_connected = registered_handlers["on_client_connected"]

            # Launch on_connected in background task
            conn_task = asyncio.create_task(on_connected(mock_transport, None))

            # Give the event loop a few ticks
            await asyncio.sleep(0.02)

            # Before answer event is set, recording and greeting MUST NOT be triggered
            self.assertFalse(conn_task.done())
            mock_audio_buffer.start_recording.assert_not_called()
            mock_engine.queue_node_opening.assert_not_called()

            # Now simulate recipient answering the call (Meta sends status: ACCEPTED)
            call_answered_event.resolve(ANSWERED)
            await asyncio.wait_for(conn_task, timeout=1.0)

            # Now recording and greeting must have been triggered
            mock_audio_buffer.start_recording.assert_awaited_once()
            mock_engine.queue_node_opening.assert_awaited_once()

    async def test_on_client_connected_runs_immediately_when_no_gating_event(self):
        """Verify on_client_connected does not block when call_answered_event is None (e.g. inbound calls)."""
        from api.services.pipecat.event_handlers import register_event_handlers

        registered_handlers = {}

        def mock_event_handler(event_name):
            def decorator(fn):
                registered_handlers[event_name] = fn
                return fn

            return decorator

        mock_transport = MagicMock()
        mock_transport.event_handler.side_effect = mock_event_handler

        mock_task = MagicMock()
        mock_task.event_handler.side_effect = mock_event_handler
        mock_task.queue_frames = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.is_call_disposed.return_value = False
        mock_engine.set_node = AsyncMock()
        mock_engine.queue_node_opening = AsyncMock()
        mock_engine.workflow.start_node_id = "start_node"

        mock_audio_buffer = MagicMock()
        mock_audio_buffer.start_recording = AsyncMock()

        with patch("api.services.pipecat.event_handlers.capture_event"):
            register_event_handlers(
                task=mock_task,
                transport=mock_transport,
                workflow_run_id=101,
                engine=mock_engine,
                audio_buffer=mock_audio_buffer,
                in_memory_logs_buffer=MagicMock(),
                transcript_log_coordinator=MagicMock(),
                pipeline_metrics_aggregator=MagicMock(),
                termination_funnel=MagicMock(),
                call_answered_event=None,
            )

            # Trigger on_pipeline_started handler so ready_state["pipeline_started"] = True
            pipeline_started_fn = registered_handlers.get("on_pipeline_started")
            if pipeline_started_fn:
                await pipeline_started_fn(mock_task, MagicMock())

            on_connected = registered_handlers["on_client_connected"]
            await on_connected(mock_transport, None)

            mock_audio_buffer.start_recording.assert_awaited_once()
            mock_engine.queue_node_opening.assert_awaited_once()


class TestWhatsAppWebRTCAnswerDecoupling(IsolatedAsyncioTestCase):
    """Test suite for decoupling WebRTC connect (SDP answer) from call answer (ACCEPTED)."""

    async def test_sdp_answer_does_not_set_call_status_in_progress(self):
        """Verify receiving SDP answer applies SDP but leaves call_status as initiated/ringing."""
        from api.services.telephony.providers.whatsapp.routes import (
            _active_connections,
            _handle_outbound_sdp_answer,
            _outbound_answered_events,
        )

        call_id = "test_call_sdp_only"
        answered_event = OutboundCallGate()
        _outbound_answered_events[call_id] = answered_event

        mock_conn = MagicMock()
        mock_conn.set_answer = AsyncMock()
        mock_conn.connect = AsyncMock()
        mock_conn.is_connected = MagicMock(return_value=True)
        mock_conn.call_status = "initiated"
        _active_connections[call_id] = (mock_conn, 100, 1, "phone_123")

        call_data = {
            "id": call_id,
            "session": {
                "sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\n",
                "sdp_type": "answer",
            },
        }

        try:
            with patch(
                "api.services.telephony.providers.whatsapp.routes._get_redis",
                AsyncMock(return_value=None),
            ):
                await _handle_outbound_sdp_answer(call_data, {})

                # SDP answer applied to peer connection
                mock_conn.set_answer.assert_awaited_once()
                # Crucial: answered_event must NOT be set yet (recipient phone is still ringing)
                self.assertFalse(answered_event.is_set())
                # Crucial: call_status must NOT be in-progress
                self.assertNotEqual(mock_conn.call_status, "in-progress")
        finally:
            _outbound_answered_events.pop(call_id, None)
            _active_connections.pop(call_id, None)

    async def test_call_accepted_sets_answered_event_and_status(self):
        """Verify receiving status: ACCEPTED sets answered_event and marks call_status in-progress."""
        from api.services.telephony.providers.whatsapp.routes import (
            _active_connections,
            _handle_call_accepted,
            _outbound_answered_events,
        )

        call_id = "test_call_accepted"
        answered_event = OutboundCallGate()
        _outbound_answered_events[call_id] = answered_event

        mock_conn = MagicMock()
        mock_conn.call_status = "initiated"
        mock_conn.connected_at = None
        _active_connections[call_id] = (mock_conn, 100, 1, "phone_123")

        mock_run = MagicMock()
        mock_run.id = 100
        mock_run.gathered_context = {}

        call_data = {
            "id": call_id,
            "timestamp": "1726000000",
        }

        try:
            with (
                patch.object(
                    db_client, "get_workflow_run", AsyncMock(return_value=mock_run)
                ),
                patch.object(
                    db_client, "update_workflow_run", AsyncMock()
                ) as mock_update_run,
                patch(
                    "api.services.telephony.providers.whatsapp.routes._get_redis",
                    AsyncMock(return_value=None),
                ),
            ):
                await _handle_call_accepted(call_data, {})

                # Answered event must be set so the pipeline greeting unblocks
                self.assertTrue(answered_event.is_set())
                self.assertEqual(mock_conn.call_status, "in-progress")
                self.assertIsNotNone(mock_conn.connected_at)
                mock_update_run.assert_awaited_once()
        finally:
            _outbound_answered_events.pop(call_id, None)
            _active_connections.pop(call_id, None)


class TestWhatsAppLivePermissions(IsolatedAsyncioTestCase):
    """Test suite for live Meta permission checks and revocation handling."""

    async def test_check_permission_detects_revocation_from_meta(self):
        """Verify check_whatsapp_permission queries Meta API and detects revoked permission."""
        from api.services.telephony.providers.whatsapp.routes import (
            check_whatsapp_permission,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
            },
        )
        mock_client = MagicMock()
        # Meta returns no_permission after user disallowed calls
        mock_client.check_call_permission = AsyncMock(
            return_value={
                "permission": {"status": "no_permission"},
                "actions": [
                    {
                        "action_name": "send_call_permission_request",
                        "can_perform_action": True,
                    }
                ],
            }
        )

        with (
            patch.object(
                db_client,
                "get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch.object(
                db_client, "get_whatsapp_call_permission", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client, "upsert_whatsapp_call_permission", AsyncMock()
            ) as mock_upsert_db,
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
        ):
            res = await check_whatsapp_permission(
                telephony_configuration_id=10,
                recipient_phone_number="+447123456789",
                current_user=mock_user,
            )

            self.assertFalse(res.can_call)
            self.assertEqual(res.status, "no_permission")
            # The no-permission branch of check_whatsapp_permission persists via
            # upsert_whatsapp_call_permission (not update_..._status_by_wa_id),
            # and stores "no_permission" as-is: only an explicit "denied" or
            # "revoked" may hard-fail this recipient's parked campaign runs.
            mock_upsert_db.assert_awaited_once_with(
                organization_id=1,
                telephony_configuration_id=10,
                phone_number_id="test_phone_id",
                recipient_phone_number="+447123456789",
                status="no_permission",
                expires_at=None,
            )

    async def test_initiate_call_blocks_when_meta_permission_revoked(self):
        """Verify initiate_call blocks and raises 400 when Meta reports permission revoked."""
        from api.services.telephony.providers.whatsapp.provider import WhatsAppProvider

        provider = WhatsAppProvider(
            {
                "access_token": "test_token",
                "phone_number_id": "test_phone_id",
                "webhook_verify_token": "test_verify_token",
                "app_secret": "test_secret",
                "from_numbers": ["+15551234567"],
                "business_initiated_calls_enabled": True,
            }
        )

        mock_client = MagicMock()
        mock_client.check_call_permission = AsyncMock(
            return_value={
                "permission": {"status": "no_permission"},
                "actions": [
                    {
                        "action_name": "send_call_permission_request",
                        "can_perform_action": True,
                    }
                ],
            }
        )

        # provider.initiate_call imports the client factory from .service at call
        # time, so the service module - not .routes - is the binding a patch has
        # to replace. The no-permission branch persists via
        # db_client.upsert_whatsapp_call_permission, so that must be stubbed too.
        with (
            patch(
                "api.services.telephony.providers.whatsapp.service.get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
            patch.object(
                db_client, "get_whatsapp_call_permission", AsyncMock(return_value=None)
            ),
            patch.object(
                db_client,
                "get_whatsapp_call_permission_by_phone_id",
                AsyncMock(return_value=None),
            ),
            patch.object(db_client, "upsert_whatsapp_call_permission", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await provider.initiate_call(
                    to_number="+447123456789",
                    webhook_url="https://example.test/webhook",
                    workflow_run_id=999,
                    organization_id=1,
                    telephony_configuration_id=10,
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn(
                "has not been granted or has been revoked", ctx.exception.detail
            )


class TestWhatsAppOfflinePermissionFallback(IsolatedAsyncioTestCase):
    """Meta's permission API is unusable; how far a stored record may stand in for it."""

    @staticmethod
    def _provider():
        from api.services.telephony.providers.whatsapp.provider import WhatsAppProvider

        return WhatsAppProvider(
            {
                "access_token": "test_token",
                "phone_number_id": "test_phone_id",
                "webhook_verify_token": "test_verify_token",
                "app_secret": "test_secret",
                "from_numbers": ["+15551234567"],
                "business_initiated_calls_enabled": True,
            }
        )

    async def _initiate_with(self, meta_behaviour, local_perm):
        provider = self._provider()
        mock_client = MagicMock()
        if isinstance(meta_behaviour, Exception):
            mock_client.check_call_permission = AsyncMock(side_effect=meta_behaviour)
        else:
            mock_client.check_call_permission = AsyncMock(return_value=meta_behaviour)
        mock_client.initiate_outbound_call = AsyncMock(
            return_value=("wacid.offline_fallback", MagicMock(), {})
        )

        # provider.initiate_call imports these from .service at call time, so the
        # service module - not .routes - is the binding a patch has to replace.
        with (
            patch(
                "api.services.telephony.providers.whatsapp.service.get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
            patch(
                "api.services.telephony.providers.whatsapp.service.register_outbound_active_connection"
            ),
            patch(
                "api.services.telephony.providers.whatsapp.service.get_whatsapp_redis",
                AsyncMock(return_value=None),
            ),
            patch.object(
                db_client,
                "get_whatsapp_call_permission",
                AsyncMock(return_value=local_perm),
            ),
            patch.object(
                db_client,
                "get_whatsapp_call_permission_by_phone_id",
                AsyncMock(return_value=local_perm),
            ),
            patch.object(db_client, "upsert_whatsapp_call_permission", AsyncMock()),
        ):
            await provider.initiate_call(
                to_number="+447123456789",
                webhook_url="https://example.test/webhook",
                workflow_run_id=999,
                organization_id=1,
                workflow_id=2,
                telephony_configuration_id=10,
            )
        return mock_client

    async def test_temporary_grant_without_expiry_is_not_trusted_offline(self):
        """A temporary grant with no recorded expiry must not read as a permission that never lapses."""
        meta_error = {
            "error": {"code": 4, "message": "Application request limit reached"}
        }
        stale = MagicMock(status="granted_temporary", expires_at=None)

        with self.assertRaises(HTTPException) as ctx:
            await self._initiate_with(meta_error, stale)
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_expired_temporary_grant_is_not_trusted_offline(self):
        meta_error = {
            "error": {"code": 4, "message": "Application request limit reached"}
        }
        expired = MagicMock(
            status="granted_temporary",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )

        with self.assertRaises(HTTPException):
            await self._initiate_with(meta_error, expired)

    async def test_unexpired_temporary_grant_is_trusted_offline(self):
        """Graceful degradation still works when we hold a concrete, future expiry."""
        live = MagicMock(
            status="granted_temporary",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
        )
        client = await self._initiate_with(ConnectionError("Meta unreachable"), live)
        client.initiate_outbound_call.assert_awaited_once()

    async def test_permanent_grant_is_trusted_offline(self):
        permanent = MagicMock(status="granted_permanent", expires_at=None)
        client = await self._initiate_with(
            ConnectionError("Meta unreachable"), permanent
        )
        client.initiate_outbound_call.assert_awaited_once()


class TestWhatsAppTelephonyCallStatus(IsolatedAsyncioTestCase):
    """Test suite for get_workflow_run_call_status in telephony API router."""

    async def test_status_reports_ringing_until_accepted(self):
        """Verify get_workflow_run_call_status reports ringing while WebRTC is connected but call not accepted."""
        from api.routes.telephony import get_workflow_run_call_status
        from api.services.telephony.providers.whatsapp.routes import _active_connections

        call_id = "wacid.test_status_call"
        mock_conn = MagicMock()
        mock_conn.is_connected = MagicMock(return_value=True)
        mock_conn.call_status = (
            "initiated"  # WebRTC connected, but status is not in-progress yet
        )
        mock_conn.connected_at = None

        _active_connections[call_id] = (mock_conn, 101, 1, "test_phone_id")

        mock_run = MagicMock()
        mock_run.id = 101
        mock_run.organization_id = 1
        mock_run.mode = "whatsapp"
        mock_run.is_completed = False
        mock_run.state = "running"
        mock_run.gathered_context = {"call_id": call_id, "provider": "whatsapp"}

        mock_user = MagicMock(selected_organization_id=1)

        try:
            with (
                patch.object(
                    db_client, "get_workflow_run", AsyncMock(return_value=mock_run)
                ),
                patch.object(db_client, "update_workflow_run", AsyncMock()),
            ):
                res = await get_workflow_run_call_status(
                    workflow_run_id=101,
                    user=mock_user,
                )
                self.assertEqual(res["status"], "ringing")
                self.assertIsNone(res["connected_at"])

            # Now mark as accepted
            mock_conn.call_status = "in-progress"
            now_str = datetime.now(timezone.utc).isoformat()
            mock_conn.connected_at = now_str
            mock_run.gathered_context = {
                "call_id": call_id,
                "provider": "whatsapp",
                "call_status": "in-progress",
                "connected_at": now_str,
            }

            with (
                patch.object(
                    db_client, "get_workflow_run", AsyncMock(return_value=mock_run)
                ),
                patch.object(db_client, "update_workflow_run", AsyncMock()),
            ):
                res = await get_workflow_run_call_status(
                    workflow_run_id=101,
                    user=mock_user,
                )
                self.assertEqual(res["status"], "connected")
                self.assertEqual(res["connected_at"], now_str)
        finally:
            _active_connections.pop(call_id, None)
