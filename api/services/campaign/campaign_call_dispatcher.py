import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import aiohttp
from loguru import logger

from api.db import db_client
from api.db.models import QueuedRunModel, WorkflowRunModel
from api.enums import WorkflowRunState
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    CallConcurrencySlot,
    call_concurrency,
)
from api.services.call_concurrency.rate_limiter import rate_limiter
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.campaign.errors import (
    CampaignRateLimitTimeout,
    ConcurrentSlotAcquisitionError,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.initial_context import merge_external_initial_context
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.utils.common import get_backend_endpoints

if TYPE_CHECKING:
    # Type-only — importing api.services.telephony eagerly triggers the
    # provider package init, which can pull in this module via the routes
    # chain and create a circular import. Runtime calls below lazy-import the
    # factory helpers inside methods instead.
    from api.services.telephony.base import TelephonyProvider


class CampaignCallDispatcher:
    """Manages rate-limited and concurrent-limited call dispatching"""

    async def get_provider_for_campaign(self, campaign) -> "TelephonyProvider":
        """Resolve and pre-flight the provider used by a campaign.

        Legacy campaigns without a pinned configuration select the first active
        outbound-ready config, preferring an explicit default. The resolved id
        is pinned on this detached campaign instance for the rest of the batch.
        """
        from api.services.telephony.factory import get_telephony_provider_by_id
        from api.services.telephony.outbound_readiness import (
            resolve_outbound_configuration_id,
        )

        requested_id = campaign.telephony_configuration_id
        resolved_id = await resolve_outbound_configuration_id(
            requested_id,
            campaign.organization_id,
            db=db_client,
        )
        if requested_id is None:
            logger.warning(
                f"Campaign {campaign.id} has no telephony_configuration_id; "
                f"using ready config {resolved_id} for org "
                f"{campaign.organization_id}"
            )
            campaign.telephony_configuration_id = resolved_id
        return await get_telephony_provider_by_id(resolved_id, campaign.organization_id)

    async def get_org_concurrent_limit(self, organization_id: int) -> int:
        """Get the concurrent call limit for an organization."""
        return await call_concurrency.get_org_concurrent_limit(organization_id)

    # Leave time for cleanup inside ARQ's 300-second job deadline.
    BATCH_TIMEOUT = 240
    SETUP_CONCURRENCY = 10
    CAPACITY_WAIT_TIMEOUT = 30

    async def process_batch(self, campaign_id: int, batch_size: int = 20) -> int:
        """Claim disjoint rows and set up calls with bounded parallelism."""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        if campaign.state != "running":
            return 0

        # Resolve legacy configurations once, and reject incomplete shared setup
        # before taking any claims or concurrency slots.
        await self.get_provider_for_campaign(campaign)
        queued_runs = await db_client.claim_queued_runs_for_processing(
            campaign_id=campaign_id,
            scheduled_before=datetime.now(UTC),
            limit=batch_size,
        )
        if not queued_runs:
            return 0

        processed_run_ids: set[int] = set()
        semaphore = asyncio.Semaphore(self.SETUP_CONCURRENCY)

        async def process_one(queued_run):
            async with semaphore:
                try:
                    slot = await self.acquire_concurrent_slot(
                        campaign.organization_id,
                        campaign,
                        timeout=self.CAPACITY_WAIT_TIMEOUT,
                    )
                    run = await self.dispatch_call(queued_run, campaign, slot)
                    # A provider accepted the call. Finish bookkeeping even if the
                    # batch is cancelled; it must never be returned for another dial.
                    await self._await_cleanup(
                        db_client.mark_campaign_run_dispatched(
                            queued_run.id,
                            run.id,
                            campaign.id,
                            campaign.organization_id,
                        )
                    )
                    processed_run_ids.add(queued_run.id)
                except (ConcurrentSlotAcquisitionError, CampaignRateLimitTimeout):
                    # Capacity contention is temporary, not a failed contact.
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"Error processing queued run {queued_run.id}: {exc}"
                    )
                    await db_client.update_queued_run(
                        queued_run_id=queued_run.id,
                        state="failed",
                        processed_at=datetime.now(UTC),
                    )

        tasks = [asyncio.create_task(process_one(row)) for row in queued_runs]
        try:
            async with asyncio.timeout(self.BATCH_TIMEOUT):
                await asyncio.gather(*tasks)
        except TimeoutError:
            logger.info(
                f"Campaign {campaign_id} batch deadline reached; deferring remaining work"
            )
        finally:
            # No claim can be returned while a sibling could still originate it.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._return_unprocessed_claims(
                queued_runs,
                processed_run_ids,
                reason="batch_finished",
            )
        return len(processed_run_ids)

    @staticmethod
    async def _await_cleanup(operation):
        """Finish a short persistence/release operation before propagating cancellation."""
        task = asyncio.create_task(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _return_unprocessed_claims(
        self,
        queued_runs: list[QueuedRunModel],
        processed_run_ids: set[int],
        *,
        reason: str,
    ) -> None:
        queued_run_ids = [
            queued_run.id
            for queued_run in queued_runs
            if queued_run.id not in processed_run_ids
        ]
        if not queued_run_ids:
            return

        try:
            returned_count = (
                await db_client.return_processing_queued_runs_without_workflow(
                    queued_run_ids
                )
            )
            logger.info(
                f"Returned {returned_count}/{len(queued_run_ids)} claimed queued runs "
                f"back to queued state; reason={reason}; "
                f"queued_run_ids={queued_run_ids}"
            )
        except Exception as revert_error:
            logger.error(
                f"Failed to return claimed queued runs; reason={reason}; "
                f"queued_run_ids={queued_run_ids}; error={revert_error}"
            )

    async def dispatch_call(
        self,
        queued_run: QueuedRunModel,
        campaign,
        concurrency_slot: CallConcurrencySlot,
    ) -> WorkflowRunModel:
        """Own the acquired slot until a provider accepts (or may have accepted) a call."""
        workflow_run = None
        attempted = False
        accepted = False
        try:
            workflow = await db_client.get_workflow(
                campaign.workflow_id,
                organization_id=campaign.organization_id,
            )
            if not workflow:
                raise ValueError(f"Workflow {campaign.workflow_id} not found")
            phone_number = queued_run.context_variables.get("phone_number")
            if not phone_number:
                raise ValueError(f"No phone number in queued run {queued_run.id}")

            provider = await self.get_provider_for_campaign(campaign)
            from_number = await rate_limiter.select_from_number(
                campaign.organization_id,
                campaign.telephony_configuration_id,
                provider.from_numbers,
            )
            initial_context = {
                **merge_external_initial_context({}, queued_run.context_variables),
                "campaign_id": campaign.id,
                "provider": provider.PROVIDER_NAME,
                "source_uuid": queued_run.source_uuid,
                "caller_number": from_number,
                "called_number": phone_number,
                "direction": "outbound",
                "telephony_configuration_id": campaign.telephony_configuration_id,
            }
            run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
            workflow_run = await db_client.create_workflow_run(
                name=f"WR-CAMPAIGN-{campaign.id}-{queued_run.id}",
                workflow_id=campaign.workflow_id,
                mode=provider.PROVIDER_NAME,
                user_id=campaign.created_by,
                initial_context=initial_context,
                campaign_id=campaign.id,
                queued_run_id=queued_run.id,
                organization_id=campaign.organization_id,
                definition_id=run_inputs.definition_id,
            )
            await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run.id)
            if queued_run.context_variables.get("is_retry"):
                reason = queued_run.context_variables.get("retry_reason", "unknown")
                await db_client.update_workflow_run(
                    run_id=workflow_run.id,
                    gathered_context={"call_tags": ["retry", f"retry_reason_{reason}"]},
                )
            quota = await authorize_workflow_run_start(
                workflow_id=campaign.workflow_id,
                organization_id=campaign.organization_id,
                workflow_run_id=workflow_run.id,
            )
            if not quota.has_quota:
                raise ValueError(quota.error_message or "Quota exceeded")

            backend_endpoint, _ = await get_backend_endpoints()
            webhook_url = (
                f"{backend_endpoint}/api/v1/telephony/{provider.WEBHOOK_ENDPOINT}"
                f"?workflow_id={campaign.workflow_id}"
                f"&workflow_run_id={workflow_run.id}"
                f"&organization_id={campaign.organization_id}"
            )
            await self.apply_rate_limit(
                campaign.organization_id,
                campaign.rate_limit_per_second,
                scope_key=f"campaign:{campaign.id}",
            )
            # No long wait or DB operation between rate admission and dialing.
            attempted = True
            call_result = await provider.initiate_call(
                to_number=phone_number,
                webhook_url=webhook_url,
                workflow_run_id=workflow_run.id,
                from_number=from_number,
                workflow_id=campaign.workflow_id,
                organization_id=campaign.organization_id,
            )
            accepted = True
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                gathered_context={
                    "provider": provider.PROVIDER_NAME,
                    **(call_result.provider_metadata or {}),
                },
            )
            return workflow_run
        except (Exception, asyncio.CancelledError) as exc:
            if accepted:
                # Bookkeeping failure cannot free capacity occupied by a real call.
                logger.exception(
                    f"Call accepted for run {workflow_run.id}; persistence interrupted"
                )
                if isinstance(exc, asyncio.CancelledError):
                    await self._await_cleanup(
                        db_client.mark_campaign_run_dispatched(
                            queued_run.id,
                            workflow_run.id,
                            campaign.id,
                            campaign.organization_id,
                        )
                    )
                    raise
                return workflow_run

            uncertain = attempted and isinstance(
                exc,
                (
                    asyncio.CancelledError,
                    TimeoutError,
                    ConnectionError,
                    aiohttp.ClientError,
                ),
            )
            await self._await_cleanup(
                self._finish_interrupted_dispatch(
                    queued_run,
                    campaign,
                    workflow_run,
                    concurrency_slot,
                    exc,
                    uncertain=uncertain,
                    attempted=attempted,
                )
            )
            raise

    async def _finish_interrupted_dispatch(
        self,
        queued_run,
        campaign,
        workflow_run,
        slot,
        error,
        *,
        uncertain: bool,
        attempted: bool,
    ) -> None:
        message = str(error) or "Call setup cancelled"
        if uncertain:
            # A request may have reached the provider. Never requeue it or free
            # a possibly live slot; terminal callbacks/stale recovery own release.
            # Save recovery first in case updating the queue fails. The slot
            # identity must survive expiration of its Redis mapping.
            uncertain_at = datetime.now(UTC)
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                gathered_context={"error": message, "call_initiation_uncertain": True},
                logs={
                    "campaign_dispatch": {
                        "outcome": "uncertain",
                        "uncertain_at": uncertain_at.isoformat(),
                        "slot_id": slot.slot_id,
                        "scope_key": slot.scope_key,
                    }
                },
            )
            await db_client.update_queued_run(
                queued_run_id=queued_run.id,
                state="failed",
                processed_at=uncertain_at,
            )
            logger.warning(
                f"Uncertain call initiation for run {workflow_run.id}; slot retained"
            )
            return

        try:
            if workflow_run:
                await db_client.update_workflow_run(
                    run_id=workflow_run.id,
                    is_completed=True,
                    state=WorkflowRunState.COMPLETED.value,
                    gathered_context={"error": message},
                    logs={
                        "campaign_dispatch": {
                            "outcome": "failed" if attempted else "not_started",
                        },
                        "telephony_status_callbacks": [
                            {
                                "status": "failed",
                                "timestamp": datetime.now(UTC).isoformat(),
                                "data": {"error": message},
                            }
                        ],
                    },
                )
                if isinstance(
                    error, (asyncio.CancelledError, CampaignRateLimitTimeout)
                ):
                    # This task owns the row and has not contacted the provider.
                    await db_client.update_queued_run(
                        queued_run_id=queued_run.id,
                        state="queued",
                    )
                if attempted:
                    await circuit_breaker.record_and_evaluate(
                        campaign.id,
                        is_failure=True,
                        workflow_run_id=workflow_run.id,
                        reason="call_initiation_failed",
                    )
        finally:
            # Also release the raw slot if cancellation interrupted mapping it.
            try:
                if workflow_run:
                    await call_concurrency.release_workflow_run_slot(workflow_run.id)
            finally:
                await call_concurrency.release_slot(slot)

    async def recover_stale_dispatches(self) -> None:
        """Settle uncertain calls without callbacks and retry durable slot cleanup."""
        pending = await db_client.recover_stale_campaign_dispatches(
            stale_before=datetime.now(UTC)
            - timedelta(seconds=rate_limiter.stale_call_timeout)
        )
        for recovery in pending:
            try:
                await rate_limiter.reconcile_workflow_slot_mapping(
                    recovery["workflow_run_id"],
                    organization_id=recovery["organization_id"],
                    slot_id=recovery["slot_id"],
                    scope_key=recovery["scope_key"],
                )
                await db_client.mark_campaign_dispatch_slot_reconciled(
                    recovery["workflow_run_id"], recovery["organization_id"]
                )
            except Exception:
                # This persisted cleanup record is scanned even after its
                # campaign completes, pauses or stops.
                logger.exception(
                    f"Stale dispatch slot cleanup failed for run "
                    f"{recovery['workflow_run_id']}; will retry"
                )

    async def apply_rate_limit(
        self,
        organization_id: int,
        rate_limit: int,
        *,
        scope_key: str,
    ) -> None:
        """Wait on the same campaign bucket used to admit actual dial attempts."""
        deadline = time.monotonic() + self.CAPACITY_WAIT_TIMEOUT
        while True:
            if await rate_limiter.acquire_token(
                organization_id,
                rate_limit,
                scope_key=scope_key,
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CampaignRateLimitTimeout(
                    "Campaign dial rate unavailable; retry later"
                )
            wait_time = await rate_limiter.get_next_available_slot(
                organization_id,
                rate_limit,
                scope_key=scope_key,
            )
            await asyncio.sleep(min(remaining, max(0.01, wait_time)))

    async def acquire_concurrent_slot(
        self, organization_id: int, campaign: any, timeout: float = 30
    ) -> CallConcurrencySlot:
        """
        Acquires a concurrent call slot - waits if necessary until a slot is available.

        Args:
            organization_id: The organization ID
            campaign: The campaign object
            timeout: Maximum time to wait for a slot (default 30 seconds)

        Returns the slot which must be released when the call completes.

        Raises:
            ConcurrentSlotAcquisitionError: If slot cannot be acquired within timeout
        """
        # Check for campaign-level max_concurrency in orchestrator_metadata.
        # It caps this campaign's own concurrent calls via a campaign-scoped
        # counter — the org-wide limit still applies on top, but calls from
        # other sources (WebRTC, inbound, other campaigns) don't count
        # against the campaign's cap.
        campaign_max_concurrency = None
        if campaign.orchestrator_metadata:
            campaign_max_concurrency = campaign.orchestrator_metadata.get(
                "max_concurrency"
            )

        try:
            return await call_concurrency.acquire_org_slot(
                organization_id,
                source=f"campaign:{campaign.id}",
                timeout=timeout,
                scope_key=(
                    f"campaign:{campaign.id}"
                    if campaign_max_concurrency is not None
                    else None
                ),
                scope_max_concurrent=campaign_max_concurrency,
                retry_interval=1,
            )
        except CallConcurrencyLimitError as e:
            raise ConcurrentSlotAcquisitionError(
                organization_id=organization_id,
                campaign_id=campaign.id,
                wait_time=e.wait_time,
            ) from e

    async def release_call_slot(self, workflow_run_id: int) -> bool:
        """Provider status callbacks release the real org/campaign reservation."""
        return await call_concurrency.release_workflow_run_slot(workflow_run_id)


# Global instance
campaign_call_dispatcher = CampaignCallDispatcher()
