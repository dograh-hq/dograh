"""Holistic test cases for WhatsApp Campaign Permission-Aware Orchestration & Real-Time Webhook Triggering.

Tests:
1. WhatsAppPermissionRequiredError exception and properties.
2. Campaign Call Dispatcher:
   - Skip mode (default): Marks workflow run with 'no_permission', marks queued run processed, bypasses circuit breaker.
   - Request & wait mode: Sends permission request, parks queued run with +24h scheduled_for, releases concurrency slot.
   - Denied state: Dispositions as 'permission_denied' when user has previously denied.
   - 24h timeout: Dispositions as 'permission_timeout' when 24h expires without permission grant.
3. Webhook Reactive Trigger:
   - Meta webhook with 'granted_temporary' reactivates parked queued run and enqueues PROCESS_CAMPAIGN_BATCH.
   - Meta webhook with 'denied' marks parked queued run as permission_denied.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from api.enums import TelephonyCallStatus
from api.services.call_concurrency.rate_limiter import FromNumberAcquisition
from api.services.telephony.providers.whatsapp.provider import (
    WhatsAppPermissionRequiredError,
    WhatsAppProvider,
)

PERMISSION_SYNC = "api.services.telephony.providers.whatsapp.permission_sync"


def patch_permission_sync(**targets):
    """Patch names inside the permission-sync module.

    Campaign permission orchestration lives in ``permission_sync`` and resolves
    ``db_client`` / ``_get_redis`` / the Meta client from *its* globals. A test
    that reaches it through ``routes`` and patches only ``routes.db_client``
    rebinds a name the code under test never reads: the mock records nothing,
    the real client is used, and every assertion about it fails - or worse,
    passes because the real call raised and the code swallowed it. Tests that
    cross that boundary patch both ends with this.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    for name, value in targets.items():
        stack.enter_context(patch(f"{PERMISSION_SYNC}.{name}", value))
    return stack


class TestWhatsAppPermissionExceptions(IsolatedAsyncioTestCase):
    """Test suite for WhatsAppPermissionRequiredError exception handling."""

    async def test_permission_error_properties(self):
        err = WhatsAppPermissionRequiredError(
            phone_number="+917505327482",
            status="no_permission",
            can_request_permission=True,
        )
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.phone_number, "+917505327482")
        self.assertEqual(err.status, "no_permission")
        self.assertTrue(err.can_request_permission)
        self.assertIn("+917505327482", err.detail)

    async def test_permission_error_denied_properties(self):
        err = WhatsAppPermissionRequiredError(
            phone_number="15551234567",
            status="denied",
            can_request_permission=False,
        )
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.phone_number, "15551234567")
        self.assertEqual(err.status, "denied")
        self.assertFalse(err.can_request_permission)


class TestWhatsAppCampaignDispatcher(IsolatedAsyncioTestCase):
    """Test suite for campaign dispatching with WhatsApp call permissions."""

    def setUp(self):
        self.mock_campaign = MagicMock()
        self.mock_campaign.id = 42
        self.mock_campaign.workflow_id = 100
        self.mock_campaign.organization_id = 1
        self.mock_campaign.created_by = 1
        self.mock_campaign.telephony_configuration_id = 5
        self.mock_campaign.orchestrator_metadata = {}

        self.mock_queued_run = MagicMock()
        self.mock_queued_run.id = 501
        self.mock_queued_run.source_uuid = "lead-501"
        self.mock_queued_run.context_variables = {"phone_number": "+917505327482"}
        self.mock_queued_run.retry_reason = None
        self.mock_queued_run.scheduled_for = None
        self.mock_queued_run.workflow_run_id = None

        self.mock_slot = MagicMock()

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.mark_workflow_run_failed", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_skips_unpermitted_lead_without_circuit_breaker_trip(
        self, mock_db, mock_mark_failed, mock_record_cb
    ):
        """Mode: 'skip' (default). Unpermitted lead is stamped no_permission and does not trip circuit breaker."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "skip"}

        # Mock workflow and run creation
        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock(return_value=MagicMock(id=999, logs={}))
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=None)
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        # Mock provider that raises WhatsAppPermissionRequiredError on initiate_call
        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        mock_provider.initiate_call = AsyncMock(
            side_effect=WhatsAppPermissionRequiredError(
                phone_number="+917505327482",
                status="no_permission",
                can_request_permission=True,
            )
        )

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch.object(dispatcher, "release_call_slot", new_callable=AsyncMock), \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            result = await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            # Workflow run should be finalized with NO_PERMISSION disposition
            mock_mark_failed.assert_called_once()
            _, kwargs = mock_mark_failed.call_args
            self.assertEqual(kwargs.get("disposition"), TelephonyCallStatus.NO_PERMISSION.value)

            # Queued run should be marked processed to prevent retrying
            mock_db.update_queued_run.assert_called_once()
            self.assertEqual(mock_db.update_queued_run.call_args[1].get("state"), "processed")

            # Circuit breaker must record is_failure=False
            mock_record_cb.assert_called_once()
            self.assertFalse(mock_record_cb.call_args[1].get("is_failure"))
            self.assertEqual(mock_record_cb.call_args[1].get("reason"), "whatsapp_permission_required")

            # dispatch_call finished the run itself, so process_batch must be
            # able to tell that apart from a run another batch completed.
            self.assertTrue(result.queued_run_finalized)

            self.assertIsNotNone(result.workflow_run)

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.mark_workflow_run_failed", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_request_and_wait_parks_lead(
        self, mock_db, mock_mark_failed, mock_record_cb
    ):
        """Mode: 'request_and_wait'. Sends permission request, parks queued run for 24h, releases slot."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "request_and_wait"}

        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock(return_value=MagicMock(id=999, logs={}))
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=None)
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        mock_provider.send_call_permission_request = AsyncMock(return_value={"messages": [{"id": "msg_123"}]})
        mock_provider.initiate_call = AsyncMock(
            side_effect=WhatsAppPermissionRequiredError(
                phone_number="+917505327482",
                status="no_permission",
                can_request_permission=True,
            )
        )

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch.object(dispatcher, "release_call_slot", new_callable=AsyncMock) as mock_release_slot, \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            result = await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            # Permission request must be sent to lead
            mock_provider.send_call_permission_request.assert_called_once_with(
                to_number="+917505327482",
                organization_id=1,
                telephony_configuration_id=5,
            )

            # Queued run must be parked with 24-hour scheduled_for
            mock_db.update_queued_run.assert_called_once()
            update_kwargs = mock_db.update_queued_run.call_args[1]
            self.assertEqual(update_kwargs.get("state"), "queued")
            self.assertEqual(update_kwargs.get("retry_reason"), "awaiting_whatsapp_permission")
            self.assertIsNotNone(update_kwargs.get("scheduled_for"))

            # Workflow run updated with awaiting_permission without marking completed
            mock_mark_failed.assert_not_called()
            mock_db.update_workflow_run.assert_called()
            update_run_kwargs = mock_db.update_workflow_run.call_args[1]
            self.assertFalse(update_run_kwargs.get("is_completed"))
            self.assertEqual(
                update_run_kwargs.get("gathered_context", {}).get("call_disposition"),
                TelephonyCallStatus.AWAITING_PERMISSION.value,
            )

            # Concurrency slot released immediately so campaign moves to next lead
            mock_release_slot.assert_called_once_with(999)

            # Circuit breaker recorded as not failure
            self.assertFalse(mock_record_cb.call_args[1].get("is_failure"))
            self.assertIsNotNone(result.workflow_run)
            # Parked, not finalized: a later batch still has to dial it.
            self.assertFalse(result.queued_run_finalized)

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.mark_workflow_run_failed", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_does_not_park_when_permission_request_fails(
        self, mock_db, mock_mark_failed, mock_record_cb
    ):
        """A request that never reached the recipient must not park the lead for 24h."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "request_and_wait"}

        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock(return_value=MagicMock(id=999, logs={}))
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=None)
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        mock_provider.send_call_permission_request = AsyncMock(
            side_effect=RuntimeError("Meta API unavailable")
        )
        mock_provider.initiate_call = AsyncMock(
            side_effect=WhatsAppPermissionRequiredError(
                phone_number="+917505327482",
                status="no_permission",
                can_request_permission=True,
            )
        )

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch.object(dispatcher, "release_call_slot", new_callable=AsyncMock), \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            # Not parked: no 24h reschedule, no awaiting_* retry_reason
            update_kwargs = mock_db.update_queued_run.call_args[1]
            self.assertEqual(update_kwargs.get("state"), "processed")
            self.assertIsNone(update_kwargs.get("scheduled_for"))
            self.assertIsNone(update_kwargs.get("retry_reason"))

            # Run failed, and the send failure is visible in the error
            mock_mark_failed.assert_called_once()
            self.assertIn("Meta API unavailable", mock_mark_failed.call_args[0][1])

            # An undeliverable request is an outage, so the breaker counts it
            self.assertTrue(mock_record_cb.call_args[1].get("is_failure"))
            self.assertEqual(
                mock_record_cb.call_args[1].get("reason"), "permission_request_failed"
            )

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.mark_workflow_run_failed", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_does_not_park_when_provider_cannot_request_permission(
        self, mock_db, mock_mark_failed, mock_record_cb
    ):
        """A provider that does not implement the consent hook must not park the lead either."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "request_and_wait"}

        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock(return_value=MagicMock(id=999, logs={}))
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=None)
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        # What TelephonyProvider.send_call_permission_request does by default
        mock_provider.send_call_permission_request = AsyncMock(
            side_effect=NotImplementedError("whatsapp cannot send call permission requests")
        )
        mock_provider.initiate_call = AsyncMock(
            side_effect=WhatsAppPermissionRequiredError(
                phone_number="+917505327482",
                status="no_permission",
                can_request_permission=True,
            )
        )

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch.object(dispatcher, "release_call_slot", new_callable=AsyncMock), \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            update_kwargs = mock_db.update_queued_run.call_args[1]
            self.assertEqual(update_kwargs.get("state"), "processed")
            self.assertIsNone(update_kwargs.get("scheduled_for"))
            mock_mark_failed.assert_called_once()

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_reuses_existing_workflow_run_when_permission_granted(
        self, mock_db, mock_record_cb
    ):
        """When permission is granted, dispatcher reuses the existing incomplete workflow run instead of creating a duplicate."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "request_and_wait"}

        # Queued run already has workflow_run_id from previous permission request
        self.mock_queued_run.workflow_run_id = 999
        self.mock_queued_run.retry_reason = "permission_granted"

        mock_existing_run = MagicMock(id=999, is_completed=False, gathered_context={"call_disposition": "awaiting_permission"})
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=mock_existing_run)
        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock()
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        mock_provider.initiate_call = AsyncMock(return_value=MagicMock(call_id="wa_call_123", provider_metadata={}))

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            result = await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            # Assert create_workflow_run was NEVER called (no duplicate run created!)
            mock_db.create_workflow_run.assert_not_called()

            # Assert initiate_call used the existing workflow run ID
            mock_provider.initiate_call.assert_called_once()
            self.assertEqual(mock_provider.initiate_call.call_args[1].get("workflow_run_id"), 999)

            # Returned run is the same existing run
            self.assertEqual(result.workflow_run.id, 999)
            # Nothing was finalized: the call went out.
            self.assertFalse(result.queued_run_finalized)

    @patch("api.services.campaign.campaign_call_dispatcher.circuit_breaker.record_and_evaluate", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.mark_workflow_run_failed", new_callable=AsyncMock)
    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_dispatcher_timeout_dispositions_as_permission_timeout(
        self, mock_db, mock_mark_failed, mock_record_cb
    ):
        """Lead previously parked awaiting permission reached 24h timeout without grant."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        dispatcher = CampaignCallDispatcher()
        self.mock_campaign.orchestrator_metadata = {"whatsapp_permission_action": "request_and_wait"}

        # Simulate queued run that already had awaiting_whatsapp_permission
        self.mock_queued_run.retry_reason = "awaiting_whatsapp_permission"

        mock_db.get_workflow = AsyncMock(return_value=MagicMock(id=100))
        mock_db.create_workflow_run = AsyncMock(return_value=MagicMock(id=999, logs={}))
        mock_db.get_workflow_run_by_queued_run_id = AsyncMock(return_value=None)
        mock_db.update_workflow_run = AsyncMock()
        mock_db.update_queued_run = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.PROVIDER_NAME = "whatsapp"
        mock_provider.WEBHOOK_ENDPOINT = "whatsapp/webhook"
        mock_provider.send_call_permission_request = AsyncMock()
        mock_provider.initiate_call = AsyncMock(
            side_effect=WhatsAppPermissionRequiredError(
                phone_number="+917505327482",
                status="no_permission",
                can_request_permission=True,
            )
        )

        with patch.object(dispatcher, "get_provider_for_campaign", return_value=mock_provider), \
             patch.object(dispatcher, "acquire_from_number_with_token", return_value=FromNumberAcquisition("15551882279", "1700000000.0")), \
             patch.object(dispatcher, "release_call_slot", new_callable=AsyncMock), \
             patch("api.services.campaign.campaign_call_dispatcher.call_concurrency") as mock_concurrency, \
             patch("api.services.campaign.campaign_call_dispatcher.rate_limiter") as mock_rate_limiter, \
             patch("api.services.campaign.campaign_call_dispatcher.authorize_workflow_run_start", return_value=MagicMock(has_quota=True)):

            mock_concurrency.bind_workflow_run = AsyncMock()
            mock_rate_limiter.store_workflow_from_number_mapping = AsyncMock()

            result = await dispatcher.dispatch_call(
                queued_run=self.mock_queued_run,
                campaign=self.mock_campaign,
                concurrency_slot=self.mock_slot,
            )

            # Did NOT send another permission request
            mock_provider.send_call_permission_request.assert_not_called()

            # Dispositioned with PERMISSION_TIMEOUT
            mock_mark_failed.assert_called_once()
            self.assertEqual(
                mock_mark_failed.call_args[1].get("disposition"),
                TelephonyCallStatus.PERMISSION_TIMEOUT.value,
            )

            # Marked processed to prevent infinite looping
            self.assertEqual(mock_db.update_queued_run.call_args[1].get("state"), "processed")
            self.assertFalse(mock_record_cb.call_args[1].get("is_failure"))

    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_process_batch_leaves_parked_run_queued(self, mock_db):
        """When a run is parked awaiting WhatsApp permission, process_batch must NOT mark it as processed."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
            DispatchResult,
        )

        dispatcher = CampaignCallDispatcher()
        mock_campaign = MagicMock(id=42, organization_id=1, state="running", processed_rows=0, telephony_configuration_id=5, rate_limit_per_second=10)
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.claim_queued_runs_for_processing = AsyncMock(return_value=[self.mock_queued_run])

        mock_wf_run = MagicMock(id=999)
        # Mock get_queued_run_by_id returning the parked state
        mock_parked = MagicMock(id=501, state="queued", retry_reason="awaiting_whatsapp_permission")
        mock_db.get_queued_run_by_id = AsyncMock(return_value=mock_parked)
        mock_db.update_queued_run = AsyncMock()
        mock_db.update_campaign = AsyncMock()

        with patch.object(dispatcher, "get_provider_for_campaign", new_callable=AsyncMock, return_value=MagicMock(from_numbers=[])), \
             patch.object(dispatcher, "apply_rate_limit", new_callable=AsyncMock), \
             patch.object(dispatcher, "acquire_concurrent_slot", new_callable=AsyncMock), \
             patch.object(dispatcher, "dispatch_call", new_callable=AsyncMock, return_value=DispatchResult(mock_wf_run)):

            processed_count = await dispatcher.process_batch(campaign_id=42, batch_size=10)

            # Queued run was NOT marked processed and not updated in process_batch
            mock_db.update_queued_run.assert_not_called()
            # Processed count is 0 because the call is parked awaiting permission
            self.assertEqual(processed_count, 0)
            mock_db.update_campaign.assert_not_called()

    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_process_batch_counts_run_finalized_by_dispatch(self, mock_db):
        """A run dispatch_call already finished (permission denied/timed out) counts as processed."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
            DispatchResult,
        )

        dispatcher = CampaignCallDispatcher()
        mock_campaign = MagicMock(id=42, organization_id=1, state="running", processed_rows=0, telephony_configuration_id=5, rate_limit_per_second=10)
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.claim_queued_runs_for_processing = AsyncMock(return_value=[self.mock_queued_run])

        # dispatch_call's denial path: the row is already "processed", so the
        # ownership-guarded update below would claim nothing.
        mock_finished = MagicMock(id=501, state="processed", retry_reason=None)
        mock_db.get_queued_run_by_id = AsyncMock(return_value=mock_finished)
        mock_db.mark_queued_run_processed_if_owned = AsyncMock(return_value=False)
        mock_db.sync_campaign_processed_rows = AsyncMock(return_value=1)

        async def finalizing_dispatch(*args, **kwargs):
            return DispatchResult(MagicMock(id=999), queued_run_finalized=True)

        with patch.object(dispatcher, "get_provider_for_campaign", new_callable=AsyncMock, return_value=MagicMock(from_numbers=[])), \
             patch.object(dispatcher, "apply_rate_limit", new_callable=AsyncMock), \
             patch.object(dispatcher, "acquire_concurrent_slot", new_callable=AsyncMock), \
             patch.object(dispatcher, "dispatch_call", side_effect=finalizing_dispatch):

            processed_count = await dispatcher.process_batch(campaign_id=42, batch_size=10)

            # Counted, and not re-claimed: dispatch already owns the transition.
            self.assertEqual(processed_count, 1)
            mock_db.mark_queued_run_processed_if_owned.assert_not_called()
            mock_db.sync_campaign_processed_rows.assert_awaited_once_with(42)

    @patch("api.services.campaign.campaign_call_dispatcher.db_client")
    async def test_process_batch_claims_run_when_dispatch_did_not_finalize(self, mock_db):
        """Normal dial: process_batch owns the processed transition via the ownership guard."""
        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
            DispatchResult,
        )

        dispatcher = CampaignCallDispatcher()
        mock_campaign = MagicMock(id=42, organization_id=1, state="running", processed_rows=0, telephony_configuration_id=5, rate_limit_per_second=10)
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)
        mock_db.claim_queued_runs_for_processing = AsyncMock(return_value=[self.mock_queued_run])

        mock_claimed = MagicMock(id=501, state="processing", retry_reason=None)
        mock_db.get_queued_run_by_id = AsyncMock(return_value=mock_claimed)
        mock_db.mark_queued_run_processed_if_owned = AsyncMock(return_value=True)
        mock_db.sync_campaign_processed_rows = AsyncMock(return_value=1)

        with patch.object(dispatcher, "get_provider_for_campaign", new_callable=AsyncMock, return_value=MagicMock(from_numbers=[])), \
             patch.object(dispatcher, "apply_rate_limit", new_callable=AsyncMock), \
             patch.object(dispatcher, "acquire_concurrent_slot", new_callable=AsyncMock), \
             patch.object(dispatcher, "dispatch_call", new_callable=AsyncMock, return_value=DispatchResult(MagicMock(id=999))):

            processed_count = await dispatcher.process_batch(campaign_id=42, batch_size=10)

            # A stand-in workflow run answers to any attribute; the finalization
            # signal must come from the call's own result.
            self.assertEqual(processed_count, 1)
            mock_db.mark_queued_run_processed_if_owned.assert_awaited_once_with(501)


class TestWhatsAppWebhookReactiveTrigger(IsolatedAsyncioTestCase):
    """Test suite for reactive dialing trigger upon receiving Meta webhook."""

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_webhook_granted_temporary_reactivates_queued_run_and_enqueues_batch(
        self, mock_db, mock_get_redis
    ):
        """When user taps 'Allow', webhook fires granted_temporary and wakes up campaign immediately."""
        from api.services.telephony.providers.whatsapp.routes import (
            _handle_user_call_permissions_change,
        )

        mock_get_redis.return_value = AsyncMock()
        mock_db.update_whatsapp_call_permission_status_by_wa_id = AsyncMock()

        # Parked queued run waiting for permission
        mock_parked_run = MagicMock()
        mock_parked_run.id = 888
        mock_parked_run.campaign_id = 42
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(
            return_value=[mock_parked_run]
        )
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock()

        # The grant is scoped to the configuration that owns this business
        # number; a run whose campaign belongs to another one is not touched.
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(
            return_value=MagicMock(id=7)
        )

        # Campaign is currently running
        mock_campaign = MagicMock(id=42, state="running", telephony_configuration_id=7)
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            change_value = {
                "user_call_permissions": [
                    {
                        "user_wa_id": "917505327482",
                        "status": "granted_temporary",
                        "expiration": str(int(datetime.now(timezone.utc).timestamp()) + 86400),
                    }
                ]
            }
            metadata = {"phone_number_id": "1057750320752614"}

            await _handle_user_call_permissions_change(change_value, metadata)

            # DB permission updated
            mock_db.update_whatsapp_call_permission_status_by_wa_id.assert_called_once()

            # Parked run activated
            mock_db.activate_queued_run_for_immediate_dial.assert_called_once_with(888)

            # PROCESS_CAMPAIGN_BATCH enqueued immediately
            mock_enqueue.assert_called_once_with(
                "process_campaign_batch",
                42,
                10,
            )

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_webhook_with_unsupported_status_leaves_permission_record_alone(
        self, mock_db, mock_get_redis
    ):
        """A malformed or future webhook status must not overwrite a valid grant."""
        from api.services.telephony.providers.whatsapp.routes import (
            _handle_user_call_permissions_change,
        )

        mock_get_redis.return_value = AsyncMock()
        mock_db.update_whatsapp_call_permission_status_by_wa_id = AsyncMock()
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[])

        for bad_status in (None, "", "some_future_meta_state"):
            with self.subTest(status=bad_status):
                mock_db.update_whatsapp_call_permission_status_by_wa_id.reset_mock()
                change_value = {
                    "user_call_permissions": [
                        {"user_wa_id": "917505327482", "status": bad_status}
                    ]
                }
                await _handle_user_call_permissions_change(
                    change_value, {"phone_number_id": "1057750320752614"}
                )
                mock_db.update_whatsapp_call_permission_status_by_wa_id.assert_not_called()

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_webhook_status_aliases_are_stored_canonically(
        self, mock_db, mock_get_redis
    ):
        """Meta's 'permanent'/'granted' spellings land as the canonical stored states."""
        from api.services.telephony.providers.whatsapp.routes import (
            _handle_user_call_permissions_change,
        )

        mock_get_redis.return_value = AsyncMock()
        mock_db.update_whatsapp_call_permission_status_by_wa_id = AsyncMock()
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[])

        for raw, expected in (("permanent", "granted_permanent"), ("granted", "granted_temporary")):
            with self.subTest(status=raw):
                mock_db.update_whatsapp_call_permission_status_by_wa_id.reset_mock()
                await _handle_user_call_permissions_change(
                    {"user_call_permissions": [{"user_wa_id": "917505327482", "status": raw}]},
                    {"phone_number_id": "1057750320752614"},
                )
                kwargs = mock_db.update_whatsapp_call_permission_status_by_wa_id.call_args.kwargs
                self.assertEqual(kwargs["status"], expected)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_webhook_denied_marks_queued_run_failed(
        self, mock_db, mock_get_redis
    ):
        """When user taps 'Disallow', webhook fires denied and marks queued run as permission_denied."""
        from api.services.telephony.providers.whatsapp.routes import (
            _handle_user_call_permissions_change,
        )

        mock_get_redis.return_value = AsyncMock()
        mock_db.update_whatsapp_call_permission_status_by_wa_id = AsyncMock()

        mock_parked_run = MagicMock()
        mock_parked_run.id = 888
        mock_parked_run.campaign_id = 42
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(
            return_value=[mock_parked_run]
        )
        mock_db.fail_queued_run_permission_denied = AsyncMock()
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(
            return_value=MagicMock(id=7)
        )
        mock_db.get_campaign_by_id = AsyncMock(
            return_value=MagicMock(id=42, state="running", telephony_configuration_id=7)
        )

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            change_value = {
                "user_call_permissions": [
                    {
                        "user_wa_id": "917505327482",
                        "status": "denied",
                    }
                ]
            }
            metadata = {"phone_number_id": "1057750320752614"}

            await _handle_user_call_permissions_change(change_value, metadata)

            # Queued run marked failed with permission_denied
            mock_db.fail_queued_run_permission_denied.assert_called_once_with(888)

            # Did NOT enqueue batch
            mock_enqueue.assert_not_called()

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_interactive_call_permission_reply_reactivates_campaign(
        self, mock_db, mock_get_redis
    ):
        """When user taps 'Allow call' in WhatsApp, Meta sends interactive call_permission_reply which reactivates campaign."""
        from fastapi import Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_get_redis.return_value = AsyncMock()
        mock_db.update_whatsapp_call_permission_status_by_message_id = AsyncMock(return_value=MagicMock())
        mock_db.update_whatsapp_call_permission_status_by_wa_id = AsyncMock()

        mock_parked_run = MagicMock(id=888, campaign_id=42)
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(
            return_value=[mock_parked_run]
        )
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock()
        mock_db.get_campaign_by_id = AsyncMock(
            return_value=MagicMock(id=42, state="running", telephony_configuration_id=1)
        )
        mock_config = MagicMock(
            id=1,
            credentials={"app_secret": "test_app_secret"},
        )
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(
            return_value=mock_config
        )

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "2349713195497707",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "1057750320752614"},
                                "messages": [
                                    {
                                        "from": "917505327482",
                                        "id": "wamid.HBgLOT...",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "call_permission_reply",
                                            "call_permission_reply": {
                                                "response": "accept",
                                                "is_permanent": False,
                                                "expiration_timestamp": "1789735027",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        import hashlib
        import hmac
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = hmac.new(b"test_app_secret", body_bytes, hashlib.sha256).hexdigest()

        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=body_bytes)
        mock_request.headers = {"x-hub-signature-256": f"sha256={sig}"}

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            res = await handle_whatsapp_webhook(mock_request)
            self.assertEqual(res, {"status": "success"})

            # Run activated for immediate dial
            mock_db.activate_queued_run_for_immediate_dial.assert_called_once_with(888)
            # Batch enqueued
            mock_enqueue.assert_called_once_with("process_campaign_batch", 42, 10)

    @patch("api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client")
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_check_permission_route_reactivates_campaign_when_granted(
        self, mock_db, mock_get_client
    ):
        """When checking permission via GET /permissions/check, if Meta reports granted, parked campaign runs are reactivated."""
        from api.services.telephony.providers.whatsapp.routes import (
            check_whatsapp_permission,
        )

        mock_config = MagicMock(
            id=5,
            provider="whatsapp",
            organization_id=1,
            credentials={"phone_number_id": "1057750320752614", "access_token": "token123"},
        )
        mock_db.get_telephony_configuration_for_org = AsyncMock(return_value=mock_config)
        mock_db.get_whatsapp_call_permission = AsyncMock(return_value=None)
        mock_db.upsert_whatsapp_call_permission = AsyncMock()

        # Mock Meta client returning start_call action allowed
        mock_client = AsyncMock()
        mock_client.check_call_permission = AsyncMock(
            return_value={
                "messaging_product": "whatsapp",
                "permission": {"status": "temporary", "expiration_time": 1789735027},
                "actions": [
                    {"action_name": "start_call", "can_perform_action": True},
                    {"action_name": "send_call_permission_request", "can_perform_action": True},
                ],
            }
        )
        mock_get_client.return_value = mock_client

        mock_parked_run = MagicMock(id=888, campaign_id=42)
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(
            return_value=[mock_parked_run]
        )
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock()
        mock_db.get_campaign_by_id = AsyncMock(
            return_value=MagicMock(id=42, state="running", telephony_configuration_id=5)
        )

        mock_user = MagicMock(selected_organization_id=1)

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            resp = await check_whatsapp_permission(
                telephony_configuration_id=5,
                recipient_phone_number="+917505327482",
                current_user=mock_user,
            )

            self.assertTrue(resp.can_call)
            self.assertEqual(resp.status, "granted_temporary")
            mock_db.activate_queued_run_for_immediate_dial.assert_called_once_with(888)
            mock_enqueue.assert_called_once_with("process_campaign_batch", 42, 10)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    @patch("api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client")
    async def test_sync_whatsapp_permissions_for_campaign_reactivates_parked_leads(
        self, mock_get_client, mock_db, mock_get_redis
    ):
        """sync_whatsapp_permissions_for_campaign checks Meta Graph API and reactivates granted runs."""
        from api.services.telephony.providers.whatsapp.routes import (
            sync_whatsapp_permissions_for_campaign,
        )

        mock_get_redis.return_value = None  # No redis cooldown in test

        mock_parked = MagicMock(
            id=101,
            campaign_id=20,
            context_variables={"phone_number": "+917505327482"},
        )
        mock_db.get_all_queued_runs_awaiting_whatsapp_permission = AsyncMock(
            return_value=[mock_parked]
        )
        mock_campaign = MagicMock(id=20, state="running", telephony_configuration_id=2)
        mock_db.get_campaign_by_id = AsyncMock(return_value=mock_campaign)

        mock_config = MagicMock(
            id=2,
            organization_id=1,
            provider="whatsapp",
            credentials={"phone_number_id": "1057750320752614", "access_token": "token123"},
        )
        mock_db.get_telephony_configuration = AsyncMock(return_value=mock_config)
        mock_db.upsert_whatsapp_call_permission = AsyncMock()
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[mock_parked])
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock()

        mock_client = AsyncMock()
        mock_client.check_call_permission = AsyncMock(
            return_value={
                "messaging_product": "whatsapp",
                "permission": {"status": "permanent"},
                "actions": [{"action_name": "start_call", "can_perform_action": True}],
            }
        )
        mock_get_client.return_value = mock_client

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(
                 db_client=mock_db,
                 _get_redis=mock_get_redis,
                 _get_or_create_whatsapp_client=mock_get_client,
             ):
            result = await sync_whatsapp_permissions_for_campaign(20, force=True)

            # The helper now reports throttling alongside the count so a caller
            # can tell "cooldown skipped this" from "Meta said nobody granted".
            self.assertEqual(result.reactivated, 1)
            self.assertFalse(result.throttled)
            mock_client.check_call_permission.assert_called_once_with("917505327482")
            mock_db.activate_queued_run_for_immediate_dial.assert_called_once_with(101)
            mock_enqueue.assert_called_once_with("process_campaign_batch", 20, 10)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_sync_whatsapp_permissions_for_campaign_does_not_arm_cooldown_when_no_work(
        self, mock_db, mock_get_redis
    ):
        """sync_whatsapp_permissions_for_campaign does not claim or arm Redis cooldown when there are no parked runs."""
        from api.services.telephony.providers.whatsapp.routes import (
            sync_whatsapp_permissions_for_campaign,
        )

        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis

        # No parked runs
        mock_db.get_all_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[])

        with patch_permission_sync(db_client=mock_db, _get_redis=mock_get_redis):
            result = await sync_whatsapp_permissions_for_campaign(20, force=False)

        self.assertEqual(result.reactivated, 0)
        self.assertFalse(result.throttled)
        # The parked-run read is what decided there was nothing to do - proof
        # the mock was actually reached rather than an exception being
        # swallowed into the same empty result.
        mock_db.get_all_queued_runs_awaiting_whatsapp_permission.assert_awaited_once()
        # Cooldown must NOT have been armed
        mock_redis.set.assert_not_called()


class TestWhatsAppPermissionFixes(IsolatedAsyncioTestCase):
    """Test suite verifying fixes for WhatsApp outbound permissions and campaign orchestration."""

    def test_parse_whatsapp_expiration_formats(self):
        """Issue 3: Expiration parsing handles unix int, float, string numeric, and ISO-8601 strings."""
        from api.services.telephony.providers.whatsapp.config import (
            parse_whatsapp_expiration,
        )

        # Unix integer
        dt1 = parse_whatsapp_expiration(1789735027)
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.tzinfo, timezone.utc)

        # Unix string integer
        dt2 = parse_whatsapp_expiration("1789735027")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2, dt1)

        # ISO-8601 string
        dt3 = parse_whatsapp_expiration("2026-09-18T12:37:07Z")
        self.assertIsNotNone(dt3)
        self.assertEqual(dt3.tzinfo, timezone.utc)
        self.assertEqual(dt3.year, 2026)

        # Invalid/empty/none returns None without raising
        self.assertIsNone(parse_whatsapp_expiration(None))
        self.assertIsNone(parse_whatsapp_expiration(""))
        self.assertIsNone(parse_whatsapp_expiration("invalid-date"))

    # provider.send_call_permission_request imports the client factory from
    # .service at call time, so .routes is not the binding it reads.
    @patch("api.services.telephony.providers.whatsapp.service.get_or_create_whatsapp_client")
    @patch("api.db.db_client")
    async def test_send_permission_request_persists_pending_record(self, mock_db, mock_get_client):
        """Issue 1: send_call_permission_request persists pending WhatsAppCallPermissionModel with message ID."""
        mock_db.upsert_whatsapp_call_permission = AsyncMock()

        mock_client = AsyncMock()
        mock_client.send_call_permission_request = AsyncMock(
            return_value={"messages": [{"id": "wamid.HBgL12345"}]}
        )
        mock_get_client.return_value = mock_client

        provider = WhatsAppProvider(
            {
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
                "webhook_verify_token": "test_verify_token",
                "app_secret": "test_secret",
                "telephony_configuration_id": 10,
                "organization_id": 2,
            }
        )

        res = await provider.send_call_permission_request(
            to_number="+15551234567",
            body_text="Please allow us to call",
        )

        self.assertEqual(res["messages"][0]["id"], "wamid.HBgL12345")
        mock_db.upsert_whatsapp_call_permission.assert_called_once()
        call_kwargs = mock_db.upsert_whatsapp_call_permission.call_args.kwargs
        self.assertEqual(call_kwargs["status"], "pending")
        self.assertEqual(call_kwargs["meta_message_id"], "wamid.HBgL12345")
        self.assertEqual(call_kwargs["phone_number_id"], "test_phone_id")
        self.assertEqual(call_kwargs["telephony_configuration_id"], 10)
        self.assertEqual(call_kwargs["organization_id"], 2)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_denial_recounts_once_per_campaign_not_once_per_run(self, mock_db, mock_get_redis):
        """Two parked leads in one campaign produce one recount, not two.

        sync_campaign_processed_rows locks the campaign row, so a recipient
        with several parked runs would otherwise serialise one exclusive-lock
        recount per run on the webhook path.
        """
        from api.services.telephony.providers.whatsapp.routes import (
            reactivate_campaign_runs_for_recipient,
        )

        runs = [MagicMock(id=101, campaign_id=7), MagicMock(id=102, campaign_id=7)]
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=runs)
        mock_db.fail_queued_run_permission_denied = AsyncMock(side_effect=lambda rid: MagicMock(id=rid))
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(
            return_value=MagicMock(id=10)
        )
        mock_db.get_campaign_by_id = AsyncMock(
            return_value=MagicMock(id=7, telephony_configuration_id=10, state="running")
        )
        mock_db.sync_campaign_processed_rows = AsyncMock(return_value=2)

        with patch_permission_sync(db_client=mock_db):
            failed = await reactivate_campaign_runs_for_recipient(
                phone_number="+15551234567",
                status="denied",
                phone_number_id="phone_num_10",
            )

        self.assertEqual(failed, 2)
        self.assertEqual(mock_db.fail_queued_run_permission_denied.await_count, 2)
        mock_db.sync_campaign_processed_rows.assert_awaited_once_with(7)

    async def test_fail_queued_run_permission_denied_updates_counters(self):
        """A denial owns failed_rows, and delegates processed_rows to the recompute.

        processed_rows is derived from queued-run state. This method is what
        makes that state true, so it must not also add a delta - two writers of
        one derived value cannot both be right, and the increment is what used
        to inflate campaign progress past the number of finished contacts.
        """
        from api.db.campaign_client import CampaignClient
        from api.db.models import CampaignModel, QueuedRunModel

        client = CampaignClient()

        # Mock session to test the DB update logic
        mock_session = AsyncMock()
        mock_run = MagicMock(id=555, campaign_id=42, state="queued", retry_reason="awaiting_whatsapp_permission")
        mock_campaign = MagicMock(id=42, processed_rows=5, failed_rows=2)
        mock_wf_run = MagicMock(id=1001, is_completed=False, gathered_context={})

        mock_session.get = AsyncMock(side_effect=lambda model, ident: mock_run if model == QueuedRunModel else (mock_campaign if model == CampaignModel else None))
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = mock_wf_run
        mock_wf_result = MagicMock()
        mock_wf_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_wf_result)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        with patch.object(client, "async_session") as mock_ctx, \
             patch.object(
                 client, "sync_campaign_processed_rows", new_callable=AsyncMock
             ) as mock_sync:
            mock_ctx.return_value.__aenter__.return_value = mock_session
            mock_ctx.return_value.__aexit__.return_value = None

            res = await client.fail_queued_run_permission_denied(555)

            self.assertIsNotNone(res)
            self.assertEqual(mock_run.state, "failed")
            self.assertEqual(mock_run.retry_reason, "permission_denied")
            self.assertTrue(mock_wf_run.is_completed)
            self.assertEqual(mock_wf_run.gathered_context["call_disposition"], TelephonyCallStatus.PERMISSION_DENIED.value)

            # The campaign counter this method owns is failed_rows, and it is
            # moved SQL-side so a concurrent writer of the same column is not
            # clobbered by a read-modify-write.
            campaign_updates = [
                str(call.args[0])
                for call in mock_session.execute.call_args_list
                if "UPDATE campaigns" in str(call.args[0])
            ]
            self.assertEqual(len(campaign_updates), 1)
            self.assertIn("failed_rows", campaign_updates[0])
            # No second writer for the derived column.
            self.assertNotIn("processed_rows", campaign_updates[0])
            # And it does not recompute either: the recount locks the campaign
            # row, so the caller does it once per campaign after its runs are
            # committed rather than once per run.
            mock_sync.assert_not_awaited()

    async def test_get_queued_runs_phone_normalization_and_formatting(self):
        """Issues 2 & 5: get_queued_runs_awaiting_whatsapp_permission filters in SQL and matches formatted numbers."""
        from api.db.campaign_client import CampaignClient

        client = CampaignClient()

        mock_session = AsyncMock()
        # Queued runs with various phone number formats
        run1 = MagicMock(id=1, state="queued", retry_reason="awaiting_whatsapp_permission", context_variables={"phone_number": "+1 (555) 123-4567"})
        run2 = MagicMock(id=2, state="queued", retry_reason="awaiting_whatsapp_permission", context_variables={"phone_number": "+15559876543"})
        
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [run1, run2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(client, "async_session") as mock_ctx:
            mock_ctx.return_value.__aenter__.return_value = mock_session
            mock_ctx.return_value.__aexit__.return_value = None

            # Webhook delivers bare digits "15551234567"
            matches = await client.get_queued_runs_awaiting_whatsapp_permission("15551234567")

            # SQL query was executed with candidate filter conditions
            mock_session.execute.assert_called_once()
            query_obj = mock_session.execute.call_args[0][0]
            self.assertIn("queued_runs.state", str(query_obj))
            self.assertIn("queued_runs.retry_reason", str(query_obj))

            # Matched run1 despite formatting with spaces, parentheses, and dashes
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].id, 1)

    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_message_webhook_rejected_without_signature(self, mock_db):
        """P0 Violation: message webhooks without signature header must be rejected with 403."""
        from fastapi import HTTPException, Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_config = MagicMock(credentials={"app_secret": "test_secret"})
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=mock_config)

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "12345"},
                        "messages": [{"type": "interactive"}]
                    }
                }]
            }]
        }
        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        mock_request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_message_webhook_rejected_with_invalid_signature(self, mock_db):
        """P0 Violation: message webhooks with forged signature must be rejected with 403."""
        from fastapi import HTTPException, Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_config = MagicMock(credentials={"app_secret": "test_secret"})
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=mock_config)

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "12345"},
                        "messages": [{"type": "interactive"}]
                    }
                }]
            }]
        }
        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        mock_request.headers = {"x-hub-signature-256": "sha256=forged_signature_digest"}

        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_user_call_permissions_rejected_when_configuration_not_found(self, mock_db):
        # An unknown phone_number_id has no app_secret to verify the payload
        # against, so the request is rejected as unverifiable (403) rather than
        # as unknown (404) - the signature gate runs before any lookup, and
        # answering 404 here would tell an unauthenticated caller which
        # business numbers exist.
        """P0 Violation: user_call_permissions must not bypass authentication when config lookup fails."""
        from fastapi import HTTPException, Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=None)

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "field": "user_call_permissions",
                    "value": {
                        "metadata": {"phone_number_id": "unknown_phone_id"},
                        "user_call_permissions": [{"user_wa_id": "15551234567", "status": "granted"}]
                    }
                }]
            }]
        }
        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        mock_request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_user_call_permissions_rejected_without_signature(self, mock_db):
        """P0 Violation: user_call_permissions must verify HMAC signature and reject missing signature with 403."""
        from fastapi import HTTPException, Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_config = MagicMock(credentials={"app_secret": "test_secret"})
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=mock_config)

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "field": "user_call_permissions",
                    "value": {
                        "metadata": {"phone_number_id": "12345"},
                        "user_call_permissions": [{"user_wa_id": "15551234567", "status": "granted"}]
                    }
                }]
            }]
        }
        mock_request = AsyncMock(spec=Request)
        mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
        mock_request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(mock_request)
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_reactivate_campaign_runs_scoped_to_configuration(self, mock_db, mock_get_redis):
        """Test reactivate_campaign_runs_for_recipient isolates runs by WhatsApp configuration."""
        from api.services.telephony.providers.whatsapp.routes import (
            reactivate_campaign_runs_for_recipient,
        )

        # Run 1 belongs to Campaign 1 (Config 10)
        run1 = MagicMock(id=101, campaign_id=1)
        # Run 2 belongs to Campaign 2 (Config 20)
        run2 = MagicMock(id=102, campaign_id=2)

        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[run1, run2])
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock()

        # Config 10
        mock_config_10 = MagicMock(id=10)
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=mock_config_10)

        camp1 = MagicMock(id=1, telephony_configuration_id=10, state="running")
        camp2 = MagicMock(id=2, telephony_configuration_id=20, state="running")

        async def mock_get_campaign(camp_id):
            return camp1 if camp_id == 1 else camp2

        mock_db.get_campaign_by_id = AsyncMock(side_effect=mock_get_campaign)

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            count = await reactivate_campaign_runs_for_recipient(
                phone_number="+15551234567",
                status="granted_temporary",
                phone_number_id="phone_num_10",
            )

            # Only run 1 (belonging to config 10) should be activated
            self.assertEqual(count, 1)
            mock_db.activate_queued_run_for_immediate_dial.assert_called_once_with(101)
            mock_enqueue.assert_called_once_with("process_campaign_batch", 1, 10)

    @patch("api.services.telephony.providers.whatsapp.routes._get_redis", new_callable=AsyncMock)
    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_reactivate_skips_enqueue_when_activation_claim_is_lost(self, mock_db, mock_get_redis):
        """A duplicate grant that activates nothing must not report runs or wake the campaign."""
        from api.services.telephony.providers.whatsapp.routes import (
            reactivate_campaign_runs_for_recipient,
        )

        run1 = MagicMock(id=101, campaign_id=1)
        mock_db.get_queued_runs_awaiting_whatsapp_permission = AsyncMock(return_value=[run1])
        # The conditional claim found the run no longer parked: another delivery
        # of the same grant (webhook redelivery, messages webhook, cron sync)
        # already activated it and already enqueued the batch.
        mock_db.activate_queued_run_for_immediate_dial = AsyncMock(return_value=None)

        mock_config_10 = MagicMock(id=10)
        mock_db.get_whatsapp_configuration_by_phone_number_id = AsyncMock(return_value=mock_config_10)
        mock_db.get_campaign_by_id = AsyncMock(
            return_value=MagicMock(id=1, telephony_configuration_id=10, state="running")
        )

        with patch("api.tasks.arq.enqueue_job", new_callable=AsyncMock) as mock_enqueue, \
             patch_permission_sync(db_client=mock_db):
            count = await reactivate_campaign_runs_for_recipient(
                phone_number="+15551234567",
                status="granted_temporary",
                phone_number_id="phone_num_10",
            )

            self.assertEqual(count, 0)
            mock_enqueue.assert_not_called()

    @patch("api.services.telephony.providers.whatsapp.routes.db_client")
    async def test_webhook_empty_payload_requires_and_verifies_signature(self, mock_db):
        """Test handle_whatsapp_webhook requires valid HMAC signature even for empty entry list."""
        from fastapi import HTTPException, Request

        from api.services.telephony.providers.whatsapp.routes import (
            handle_whatsapp_webhook,
        )

        mock_config = MagicMock(credentials={"app_secret": "my_secret_key"})
        mock_db.get_active_whatsapp_configurations = AsyncMock(return_value=[mock_config])

        raw_payload = b'{"object": "whatsapp_business_account", "entry": []}'

        # 1. Missing signature header -> 403
        req_no_sig = AsyncMock(spec=Request)
        req_no_sig.body = AsyncMock(return_value=raw_payload)
        req_no_sig.headers = {}
        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(req_no_sig)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Missing webhook signature")

        # 2. Invalid signature -> 403
        req_bad_sig = AsyncMock(spec=Request)
        req_bad_sig.body = AsyncMock(return_value=raw_payload)
        req_bad_sig.headers = {"x-hub-signature-256": "sha256=invalid_hash_value"}
        with self.assertRaises(HTTPException) as ctx:
            await handle_whatsapp_webhook(req_bad_sig)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Invalid webhook signature")

        # 3. Valid signature matching active config -> 200 success
        import hashlib
        import hmac
        valid_hash = hmac.new(b"my_secret_key", raw_payload, hashlib.sha256).hexdigest()
        req_valid = AsyncMock(spec=Request)
        req_valid.body = AsyncMock(return_value=raw_payload)
        req_valid.headers = {"x-hub-signature-256": f"sha256={valid_hash}"}

        resp = await handle_whatsapp_webhook(req_valid)
        self.assertEqual(resp, {"status": "success"})

    def test_acquire_from_number_default_timeout(self):
        """Verify the from_number acquisition default timeout is 600 seconds.

        Asserts against the token-returning variant, which is the one dispatch
        actually uses; the non-token sibling was removed rather than left as a
        second, unreachable copy of the same retry loop.
        """
        import inspect

        from api.services.campaign.campaign_call_dispatcher import (
            CampaignCallDispatcher,
        )

        sig = inspect.signature(CampaignCallDispatcher.acquire_from_number_with_token)
        self.assertEqual(sig.parameters["timeout"].default, 600.0)

    @patch("api.services.campaign.campaign_orchestrator.db_client")
    async def test_orchestrator_does_not_mark_complete_with_parked_runs(self, mock_db):
        """Verify orchestrator does not mark campaign complete when parked/future queued runs exist."""
        from api.db.models import CampaignModel
        from api.services.campaign.campaign_orchestrator import CampaignOrchestrator

        # CampaignOrchestrator takes the Redis client it publishes progress on.
        orchestrator = CampaignOrchestrator(AsyncMock())
        campaign = MagicMock(spec=CampaignModel)
        campaign.id = 123
        campaign.last_activity_at = datetime.now(UTC) - timedelta(hours=2)
        campaign.last_batch_scheduled_at = None
        campaign.started_at = None

        # Claimable count right now is 0 (all remaining runs are parked +24h in future)
        mock_db.get_claimable_queued_runs_count = AsyncMock(return_value=0)
        # But total queued/processing count is > 0
        mock_db.get_queued_runs_count = AsyncMock(return_value=5)

        should_complete = await orchestrator._should_mark_complete(campaign)
        self.assertFalse(should_complete)
        mock_db.get_queued_runs_count.assert_awaited_with(campaign_id=123, states=["queued", "processing"])

    @patch(f"{PERMISSION_SYNC}.sync_all_parked_whatsapp_permissions")
    async def test_sweep_parked_whatsapp_permissions_cron(self, mock_sync):
        """Verify sweep_parked_whatsapp_permissions task invokes sync_all_parked_whatsapp_permissions."""
        from api.tasks.campaign_tasks import sweep_parked_whatsapp_permissions

        mock_sync.return_value = 3
        result = await sweep_parked_whatsapp_permissions({})
        self.assertEqual(result, 3)
        mock_sync.assert_awaited_once()

    @patch("api.db.telephony_configuration_client.select")
    async def test_upsert_whatsapp_call_permission_concurrency_race(self, mock_select):
        """Verify upsert_whatsapp_call_permission recovers from IntegrityError race condition."""
        from sqlalchemy.exc import IntegrityError

        from api.db.models import WhatsAppCallPermissionModel
        from api.db.telephony_configuration_client import TelephonyConfigurationClient

        client = TelephonyConfigurationClient()
        mock_session = AsyncMock()

        # _select_permission_row reads scalars().all() - it orders by id and
        # reports duplicates rather than taking an arbitrary first row - so the
        # result mocks have to answer that call, not .first().
        first_result = MagicMock()
        first_result.scalars.return_value.all.return_value = []

        # Second query (after race) returns existing row inserted by competitor
        existing_row = MagicMock(spec=WhatsAppCallPermissionModel)
        existing_row.status = "pending"
        existing_row.id = 1
        second_result = MagicMock()
        second_result.scalars.return_value.all.return_value = [existing_row]

        mock_session.execute = AsyncMock(side_effect=[first_result, second_result])
        # First commit raises IntegrityError due to competitor commit
        mock_session.commit = AsyncMock(side_effect=[IntegrityError("stmt", "params", Exception("unique violation")), None])
        mock_session.rollback = AsyncMock()
        mock_session.refresh = AsyncMock()

        @asynccontextmanager
        async def fake_session():
            yield mock_session

        client.async_session = fake_session

        row = await client.upsert_whatsapp_call_permission(
            organization_id=1,
            telephony_configuration_id=10,
            phone_number_id="pn_1",
            recipient_phone_number="+15551234567",
            status="granted_temporary",
            permission_type="temporary",
        )

        mock_session.rollback.assert_awaited_once()
        self.assertEqual(row.status, "granted_temporary")
        self.assertEqual(row.permission_type, "temporary")




