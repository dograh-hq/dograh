"""WhatsApp call teardown: the two termination entry points must not re-enter each other.

``handle_call_terminate`` (Meta webhook, cross-worker publish, provider
``end_call``) cancels the call's pipeline task. That task's own finalizer ends
the call as well - it calls ``terminate_whatsapp_call_by_id``, which calls
``handle_call_terminate``. Without a teardown claim the cancelled pipeline
re-enters the teardown that cancelled it and issues a second Meta terminate, a
second terminate publish and a second completion write for the same call.
"""

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from api.services.telephony.providers.whatsapp import service


class TestTerminationReentrancy(IsolatedAsyncioTestCase):
    def setUp(self):
        self.call_id = "wa_call_reentrancy"
        service._pipeline_tasks.pop(self.call_id, None)
        service._active_connections.pop(self.call_id, None)
        service._outbound_answered_events.pop(self.call_id, None)
        service._pipeline_teardown_claims.pop(self.call_id, None)

    def tearDown(self):
        service._pipeline_tasks.pop(self.call_id, None)
        service._active_connections.pop(self.call_id, None)
        service._outbound_answered_events.pop(self.call_id, None)
        service._pipeline_teardown_claims.pop(self.call_id, None)

    async def _start_pipeline(self, running: asyncio.Event):
        """Run the real pipeline wrapper over a stub that blocks until cancelled."""

        async def never_finishes(*args, **kwargs):
            running.set()
            await asyncio.Event().wait()

        patcher = patch(
            "api.services.pipecat.run_pipeline.run_pipeline_smallwebrtc",
            new=never_finishes,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        task = asyncio.create_task(
            service._run_whatsapp_pipeline(
                connection=MagicMock(),
                workflow_id=1,
                workflow_run_id=999,
                user_id=1,
                organization_id=1,
                call_id=self.call_id,
            )
        )
        service._pipeline_tasks[self.call_id] = task
        await asyncio.wait_for(running.wait(), timeout=5)
        return task

    @patch("api.services.telephony.providers.whatsapp.service.call_concurrency")
    @patch("api.services.telephony.providers.whatsapp.service.db_client")
    @patch(
        "api.services.telephony.providers.whatsapp.service._get_redis",
        new_callable=AsyncMock,
    )
    async def test_cancelling_the_teardown_owner_still_finishes_the_call(
        self, mock_redis, mock_db, mock_concurrency
    ):
        """The owner's cleanup survives its own caller being cancelled.

        The pipeline defers to whoever claimed the teardown, so it is no longer
        an accidental fallback. If the owner is cancelled part-way through -
        worker shutdown, a webhook request whose client went away - an
        abandoned run would otherwise stay "running" with its concurrency slot
        held until the stale timeout.
        """
        mock_redis.return_value = None
        mock_db.get_workflow_run = AsyncMock(
            return_value=MagicMock(gathered_context={})
        )
        mock_db.update_workflow_run = AsyncMock()
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        disconnect_started = asyncio.Event()

        async def slow_disconnect():
            disconnect_started.set()
            await asyncio.sleep(0.1)

        connection = MagicMock()
        connection.disconnect = slow_disconnect
        service._active_connections[self.call_id] = (connection, 999, 1, "pn1")

        terminate = asyncio.create_task(
            service.handle_call_terminate({"id": self.call_id}, {})
        )
        await asyncio.wait_for(disconnect_started.wait(), timeout=5)

        # The caller goes away mid-disconnect.
        terminate.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await terminate

        # The cleanup it owned still lands.
        for _ in range(50):
            if mock_concurrency.release_workflow_run_slot.await_count:
                break
            await asyncio.sleep(0.02)

        mock_db.update_workflow_run.assert_awaited_once()
        self.assertTrue(mock_db.update_workflow_run.await_args.kwargs["is_completed"])
        mock_concurrency.release_workflow_run_slot.assert_awaited_once_with(999)
        self.assertNotIn(self.call_id, service._active_connections)

    @patch("api.services.telephony.providers.whatsapp.service.call_concurrency")
    @patch("api.services.telephony.providers.whatsapp.service.db_client")
    @patch(
        "api.services.telephony.providers.whatsapp.service._get_redis",
        new_callable=AsyncMock,
    )
    async def test_cancelled_pipeline_does_not_reenter_teardown(
        self, mock_redis, mock_db, mock_concurrency
    ):
        mock_redis.return_value = None
        mock_db.get_workflow_run_by_call_id = AsyncMock(return_value=None)
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        running = asyncio.Event()
        task = await self._start_pipeline(running)

        with patch(
            "api.services.telephony.providers.whatsapp.service.terminate_whatsapp_call_by_id",
            new_callable=AsyncMock,
        ) as mock_terminate:
            await service.handle_call_terminate({"id": self.call_id}, {})

        self.assertTrue(task.done())
        # The pipeline finalizer saw the teardown claim and left the call to the
        # canceller instead of starting a second one.
        mock_terminate.assert_not_awaited()
        # The claim is released by the task's own done callback, so a later,
        # genuinely separate termination of this call is not suppressed.
        await asyncio.sleep(0)
        self.assertNotIn(self.call_id, service._pipeline_teardown_claims)

    @patch("api.services.telephony.providers.whatsapp.service.call_concurrency")
    @patch("api.services.telephony.providers.whatsapp.service.db_client")
    @patch(
        "api.services.telephony.providers.whatsapp.service._get_redis",
        new_callable=AsyncMock,
    )
    async def test_claim_outlives_a_wait_that_timed_out(
        self, mock_redis, mock_db, mock_concurrency
    ):
        """A pipeline that outlasts the canceller's wait must still not re-terminate.

        The bounded wait exists so a pipeline ignoring cancellation cannot hang
        the caller - it abandons the task and moves on. The task's finalizer
        then runs later and ends the call all over again unless the claim is
        tied to the task's lifetime rather than to how long anyone waited.
        """
        mock_redis.return_value = None
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        running = asyncio.Event()
        finalizer_reached = asyncio.Event()

        async def ignores_cancellation_for_a_while(*args, **kwargs):
            running.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Still winding down well after the canceller gives up.
                await asyncio.sleep(0.2)
                finalizer_reached.set()
                raise

        patcher = patch(
            "api.services.pipecat.run_pipeline.run_pipeline_smallwebrtc",
            new=ignores_cancellation_for_a_while,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        task = asyncio.create_task(
            service._run_whatsapp_pipeline(
                connection=MagicMock(),
                workflow_id=1,
                workflow_run_id=999,
                user_id=1,
                organization_id=1,
                call_id=self.call_id,
            )
        )
        service._pipeline_tasks[self.call_id] = task
        await asyncio.wait_for(running.wait(), timeout=5)

        with patch(
            "api.services.telephony.providers.whatsapp.service.terminate_whatsapp_call_by_id",
            new_callable=AsyncMock,
        ) as mock_terminate:
            # Gives up long before the pipeline stops.
            await service.unregister_outbound_active_connection(
                self.call_id, timeout=0.01
            )
            self.assertFalse(task.done())

            await asyncio.wait_for(finalizer_reached.wait(), timeout=5)
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_terminate.assert_not_awaited()

        await asyncio.sleep(0)
        self.assertNotIn(self.call_id, service._pipeline_teardown_claims)

    @patch("api.services.telephony.providers.whatsapp.service.call_concurrency")
    @patch(
        "api.services.telephony.providers.whatsapp.service._get_redis",
        new_callable=AsyncMock,
    )
    async def test_pipeline_ending_on_its_own_still_terminates(
        self, mock_redis, mock_concurrency
    ):
        mock_redis.return_value = None
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        running = asyncio.Event()
        task = await self._start_pipeline(running)

        with patch(
            "api.services.telephony.providers.whatsapp.service.terminate_whatsapp_call_by_id",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_terminate:
            # No teardown claim: nobody else is ending this call, so the
            # finalizer owns the hangup.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_terminate.assert_awaited_once()
