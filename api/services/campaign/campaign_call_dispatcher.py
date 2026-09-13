import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Optional

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.db.models import QueuedRunModel, WorkflowRunModel
from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    CallConcurrencySlot,
    call_concurrency,
)
from api.services.call_concurrency.rate_limiter import (
    FromNumberAcquisition,
    rate_limiter,
)
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.campaign.errors import (
    ConcurrentSlotAcquisitionError,
    PhoneNumberPoolExhaustedError,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.initial_context import merge_external_initial_context
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow_run_failure import mark_workflow_run_failed
from api.utils.common import get_backend_endpoints

if TYPE_CHECKING:
    # Type-only — importing api.services.telephony eagerly triggers the
    # provider package init, which can pull in this module via the routes
    # chain and create a circular import. Runtime calls below lazy-import the
    # factory helpers inside methods instead.
    from api.services.telephony.base import TelephonyProvider


# Retry reasons a queued run can carry while it is still waiting to be dialled.
# "awaiting_<provider>_permission" is written when the run is parked pending
# recipient consent; "permission_granted" is written by the activation path
# (db_client.activate_queued_run_for_immediate_dial, reached from the WhatsApp
# webhook and the Meta permission sync) once consent arrives. Both leave the
# run in state "queued" for a later batch to dial.
PERMISSION_GRANTED_RETRY_REASON = "permission_granted"


def _is_awaiting_permission_retry_reason(retry_reason: Optional[str]) -> bool:
    """True for the provider-scoped parked reason, e.g. awaiting_whatsapp_permission."""
    return bool(
        retry_reason
        and retry_reason.startswith("awaiting_")
        and retry_reason.endswith("_permission")
    )


def _is_awaiting_dial(queued_run: Optional[QueuedRunModel]) -> bool:
    """True when a queued run must stay queued for a later batch to dial it."""
    if not queued_run or queued_run.state != "queued":
        return False
    retry_reason = queued_run.retry_reason
    return retry_reason == PERMISSION_GRANTED_RETRY_REASON or (
        _is_awaiting_permission_retry_reason(retry_reason)
    )


class DispatchResult(NamedTuple):
    """What one ``dispatch_call`` did with the queued run it was given.

    ``queued_run_finalized`` is True when dispatch itself ended the run -
    permission denied, or the 24h wait for consent timed out. Those paths
    already move the row to "processed", so ``process_batch``'s
    ownership-guarded update finds nothing left to claim and would report a
    finished run as unprocessed.

    Returned rather than stamped on the workflow run or parked on the
    dispatcher: the caller reads it off the call it just made, so there is no
    process-local state to keep in sync across instances or tasks, and a
    stand-in workflow run cannot fabricate the flag by answering to any
    attribute. Unpacked as a tuple at the call site for the same reason.
    """

    workflow_run: Optional[WorkflowRunModel]
    queued_run_finalized: bool = False


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

    async def process_batch(self, campaign_id: int, batch_size: int = 10) -> int:
        """
        Processes a batch of queued runs with priority for scheduled retries.
        Thread-safe: uses SELECT FOR UPDATE SKIP LOCKED to prevent concurrent processing.
        Returns: number of processed runs
        """
        # Lazy to preserve this module's import-cycle boundary with telephony
        # provider registration. See the TYPE_CHECKING note above.
        from api.services.telephony.outbound_readiness import OutboundReadinessError

        # Get campaign details
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Check if campaign is in running state
        if campaign.state != "running":
            logger.info(
                f"Campaign {campaign_id} is not in running state: {campaign.state}"
            )
            return 0

        # Atomically claim queued runs for processing (thread-safe)
        # This uses SELECT FOR UPDATE SKIP LOCKED to prevent race conditions
        queued_runs = await db_client.claim_queued_runs_for_processing(
            campaign_id=campaign_id,
            scheduled_before=datetime.now(UTC),
            limit=batch_size,
        )

        if not queued_runs:
            logger.info(f"No more queued runs for campaign {campaign_id}")
            return 0

        # Initialize from_number pool for this campaign's telephony config.
        try:
            provider = await self.get_provider_for_campaign(campaign)
            if provider.from_numbers:
                await rate_limiter.initialize_from_number_pool(
                    campaign.organization_id,
                    provider.from_numbers,
                    telephony_configuration_id=campaign.telephony_configuration_id,
                )
        except Exception as e:
            logger.warning(f"Failed to initialize from_number pool: {e}")

        processed_count = 0
        processed_run_ids: set[int] = set()
        # Accumulated over the batch and written once, so a single stale
        # campaign snapshot can't be used to write the same value N times.
        pending_processed_rows = 0
        try:
            for i, queued_run in enumerate(queued_runs):
                try:
                    # Apply rate limiting, i.e lets not initiate more than rate_limit_per_second
                    # calls per second. It is different than concurrency limit.
                    await self.apply_rate_limit(
                        campaign.organization_id, campaign.rate_limit_per_second
                    )

                    # Acquire concurrent slot - waits until a slot is available
                    concurrency_slot = await self.acquire_concurrent_slot(
                        campaign.organization_id, campaign
                    )

                    # Dispatch the call
                    _, dispatch_finalized = await self.dispatch_call(
                        queued_run, campaign, concurrency_slot
                    )

                    # Check whether the queued run is still waiting to be dialled
                    # (e.g. parked awaiting WhatsApp call permission).
                    current_queued_run = await db_client.get_queued_run_by_id(
                        queued_run.id
                    )
                    is_awaiting_dial = _is_awaiting_dial(current_queued_run)

                    if is_awaiting_dial:
                        # Keep in queued state so a later batch dials it. This
                        # covers both the freshly parked run and one whose
                        # permission was granted between dispatch and this read
                        # (the activation path rewrites retry_reason to
                        # "permission_granted" while leaving state "queued");
                        # marking that run processed would silently drop its dial.
                        processed_run_ids.add(queued_run.id)
                        logger.info(
                            f"[Campaign {campaign_id}] Queued run {queued_run.id} stays queued "
                            f"(retry_reason={current_queued_run.retry_reason}) awaiting dial"
                        )
                    elif dispatch_finalized:
                        # dispatch_call already moved this run to "processed";
                        # it is finished by this batch and counts as such.
                        processed_count += 1
                        processed_run_ids.add(queued_run.id)
                        pending_processed_rows += 1
                    else:
                        # Conditional on this batch still owning the claim. A run
                        # that was parked, granted, then claimed and completed by
                        # another batch is no longer ours: rewriting it here
                        # would clobber that batch's processed_at.
                        claimed = await db_client.mark_queued_run_processed_if_owned(
                            queued_run.id
                        )
                        if not claimed:
                            logger.info(
                                f"[Campaign {campaign_id}] Queued run {queued_run.id} was "
                                f"already finished elsewhere (state="
                                f"{getattr(current_queued_run, 'state', None)}); leaving it alone"
                            )
                        else:
                            processed_count += 1

                        processed_run_ids.add(queued_run.id)

                        # Only a signal that this batch changed something; the
                        # actual counter is recomputed from queued-run state
                        # below, so it cannot be inflated by a double write.
                        pending_processed_rows += 1

                except asyncio.CancelledError:
                    logger.warning(
                        f"Campaign {campaign_id} batch cancelled; returning claimed "
                        "queued runs that were not dispatched"
                    )
                    await self._return_unprocessed_claims(
                        queued_runs, processed_run_ids, reason="task_cancelled"
                    )
                    raise

                except OutboundReadinessError as e:
                    logger.warning(
                        f"Outbound setup is incomplete for campaign {campaign_id}; "
                        "returning claimed queued runs without dispatching calls: "
                        f"{e}"
                    )
                    await self._return_unprocessed_claims(
                        queued_runs,
                        processed_run_ids,
                        reason="outbound_readiness_failed",
                    )
                    raise

                except PhoneNumberPoolExhaustedError as e:
                    logger.warning(
                        f"Phone number pool exhausted for campaign {campaign_id}; "
                        "returning claimed queued runs that were not dispatched: "
                        f"{e}"
                    )
                    await self._return_unprocessed_claims(
                        queued_runs,
                        processed_run_ids,
                        reason="phone_number_pool_exhausted",
                    )
                    # Re-raise to propagate to process_campaign_batch
                    raise

                except ConcurrentSlotAcquisitionError as e:
                    logger.warning(
                        f"Concurrent slot acquisition failed for campaign {campaign_id}; "
                        "returning claimed queued runs that were not dispatched: "
                        f"{e}"
                    )
                    await self._return_unprocessed_claims(
                        queued_runs,
                        processed_run_ids,
                        reason="concurrent_slot_acquisition_failed",
                    )
                    # Re-raise to propagate to process_campaign_batch
                    raise

                except Exception as e:
                    logger.warning(f"Error processing queued run {queued_run.id}: {e}")

                    # Mark the queued run as failed to prevent infinite retry loops
                    try:
                        await db_client.update_queued_run(
                            queued_run_id=queued_run.id,
                            state="failed",
                            processed_at=datetime.now(UTC),
                        )
                        logger.info(
                            f"Marked queued run {queued_run.id} as failed due to error: {e}"
                        )
                    except Exception as update_error:
                        logger.error(
                            f"Failed to mark queued run {queued_run.id} as failed: {update_error}"
                        )
        finally:
            # Flush on every exit path (including the re-raised errors above)
            # so dispatched calls are never missing from the progress counter.
            await self._flush_processed_rows(campaign_id, pending_processed_rows)

        return processed_count

    async def _flush_processed_rows(self, campaign_id: int, changed: int) -> None:
        """Recompute the campaign's processed_rows from queued-run state.

        ``changed`` is only a "did this batch finish anything" signal, not a
        delta. Maintaining the counter by increment meant three writers (batch
        dispatch, permission denial, redial) each adding their own, so a
        duplicate webhook or an overlapping batch could push progress past the
        number of finished contacts, and a failed write lost it permanently.
        Recomputing is idempotent, so neither can happen.

        Failure is survivable and deliberately not raised: the runs are already
        marked processed, queued-run state remains authoritative, and campaign
        completion is driven by queued counts rather than this number. The next
        batch's sync corrects it.
        """
        if changed <= 0:
            return

        try:
            actual = await db_client.sync_campaign_processed_rows(campaign_id)
            logger.debug(f"Campaign {campaign_id} processed_rows synced to {actual}")
        except Exception as e:
            logger.error(
                f"Failed to sync processed_rows for campaign {campaign_id}: {e}. "
                "Progress will read stale until the next batch; queued-run state "
                "remains authoritative and completion is unaffected."
            )

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
        campaign: any,
        concurrency_slot: CallConcurrencySlot,
    ) -> DispatchResult:
        """Creates workflow run and initiates call. Requires a pre-acquired slot."""
        from_number = None
        from_number_token = None
        workflow_run = None
        slot_bound = False
        # Set only by the paths below that end the queued run themselves.
        queued_run_finalized = False

        try:
            # Get workflow details
            workflow = await db_client.get_workflow(
                campaign.workflow_id,
                organization_id=campaign.organization_id,
            )
            if not workflow:
                raise ValueError(f"Workflow {campaign.workflow_id} not found")

            # Extract phone number
            phone_number = queued_run.context_variables.get("phone_number")
            if not phone_number:
                raise ValueError(f"No phone number in queued run {queued_run.id}")

            # Get provider for this campaign's pinned telephony config.
            provider = await self.get_provider_for_campaign(campaign)
            workflow_run_mode = provider.PROVIDER_NAME

            # Acquire a unique from_number from the pool scoped to this campaign's
            # telephony configuration so orgs with multiple configs don't leak
            # caller IDs across configs.
            from_number_acquisition = await self.acquire_from_number_with_token(
                campaign.organization_id,
                telephony_configuration_id=campaign.telephony_configuration_id,
            )
            if from_number_acquisition is None:
                raise PhoneNumberPoolExhaustedError(
                    organization_id=campaign.organization_id
                )
            from_number = from_number_acquisition.from_number
            from_number_token = from_number_acquisition.token

            logger.info(f"Provider name: {provider.PROVIDER_NAME}")
            logger.info(f"Queued run context: {queued_run.context_variables}")

            # Merge context variables (queued_run context already includes retry info if applicable)
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

            logger.info(f"Final initial_context: {initial_context}")

            # Create or reuse workflow run with queued_run_id tracking
            workflow_run = None
            existing_run = await db_client.get_workflow_run_by_queued_run_id(
                queued_run.id
            )
            if existing_run and not existing_run.is_completed:
                workflow_run = existing_run
                await db_client.update_workflow_run(
                    run_id=workflow_run.id,
                    initial_context=initial_context,
                    gathered_context={
                        "call_disposition": None,
                        "mapped_call_disposition": None,
                        "call_status": None,
                        "error": None,
                    },
                    state=WorkflowRunState.INITIALIZED.value,
                )
                logger.info(
                    f"[Campaign {campaign.id}] Reusing existing workflow run {workflow_run.id} "
                    f"for queued run {queued_run.id}"
                )

            if not workflow_run:
                workflow_run_name = f"WR-CAMPAIGN-{campaign.id}-{queued_run.id}"
                run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
                workflow_run = await db_client.create_workflow_run(
                    name=workflow_run_name,
                    workflow_id=campaign.workflow_id,
                    mode=workflow_run_mode,
                    user_id=campaign.created_by,
                    initial_context=initial_context,
                    campaign_id=campaign.id,
                    queued_run_id=queued_run.id,  # Link to queued run for retry tracking
                    organization_id=campaign.organization_id,
                    definition_id=run_inputs.definition_id,
                )
            await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run.id)
            slot_bound = True

            # Store from_number mapping for cleanup on call completion
            await rate_limiter.store_workflow_from_number_mapping(
                workflow_run.id,
                campaign.organization_id,
                from_number,
                telephony_configuration_id=campaign.telephony_configuration_id,
                token=from_number_token,
            )
        except Exception:
            # Release slot and from_number on error
            if slot_bound and workflow_run:
                await call_concurrency.release_workflow_run_slot(workflow_run.id)
            else:
                await call_concurrency.release_slot(concurrency_slot)
            if from_number:
                await rate_limiter.release_from_number(
                    campaign.organization_id,
                    from_number,
                    telephony_configuration_id=campaign.telephony_configuration_id,
                    expected_token=from_number_token,
                )
            raise

        # Add "retry" tag if this is a retry call
        if queued_run.context_variables.get("is_retry"):
            retry_reason = queued_run.context_variables.get("retry_reason", "unknown")
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                gathered_context={
                    "call_tags": ["retry", f"retry_reason_{retry_reason}"]
                },
            )

        quota_result = await authorize_workflow_run_start(
            workflow_id=campaign.workflow_id,
            organization_id=campaign.organization_id,
            workflow_run_id=workflow_run.id,
        )
        if not quota_result.has_quota:
            error_message = quota_result.error_message or "Quota exceeded"
            logger.warning(
                f"Campaign {campaign.id} quota check failed for workflow run "
                f"{workflow_run.id}: {error_message}"
            )
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                is_completed=True,
                state=WorkflowRunState.COMPLETED.value,
                gathered_context={"error": error_message},
            )

            await self.release_call_slot(workflow_run.id)

            raise ValueError(error_message)

        # Initiate call via telephony provider
        try:
            # Construct webhook URL with parameters
            backend_endpoint, _ = await get_backend_endpoints()
            webhook_endpoint = provider.WEBHOOK_ENDPOINT
            webhook_url = (
                f"{backend_endpoint}/api/v1/telephony/{webhook_endpoint}"
                f"?workflow_id={campaign.workflow_id}"
                f"&workflow_run_id={workflow_run.id}"
                f"&organization_id={campaign.organization_id}"
            )

            call_result = await provider.initiate_call(
                to_number=phone_number,
                webhook_url=webhook_url,
                workflow_run_id=workflow_run.id,
                from_number=from_number,
                workflow_id=campaign.workflow_id,
                organization_id=campaign.organization_id,
                telephony_configuration_id=campaign.telephony_configuration_id,
            )

            # Store provider type and metadata in gathered_context
            # (required for WebSocket handler to route to correct provider)
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                gathered_context={
                    "provider": provider.PROVIDER_NAME,
                    **(call_result.provider_metadata or {}),
                },
            )

            logger.info(
                f"Call initiated for workflow run {workflow_run.id}, Call ID: {call_result.call_id}"
            )

        except Exception as e:
            from api.services.telephony.base import TelephonyPermissionRequiredError

            if isinstance(e, TelephonyPermissionRequiredError):
                logger.info(
                    f"[{provider.PROVIDER_NAME.upper()} Campaign] Missing call permission for workflow run {workflow_run.id} "
                    f"(campaign {campaign.id}, lead: {phone_number}): {e}"
                )
                action = (campaign.orchestrator_metadata or {}).get(
                    "whatsapp_permission_action", "skip"
                )
                retry_reason = f"awaiting_{provider.PROVIDER_NAME}_permission"
                is_timeout = (
                    queued_run.retry_reason == retry_reason
                    or queued_run.retry_reason == "awaiting_whatsapp_permission"
                )

                # Parking is only justified once a request is actually on its way
                # to the recipient. If sending it fails - or the provider cannot
                # send one at all - nobody will ever be prompted, so parking for
                # 24 hours just strands the lead until the timeout.
                permission_request_sent = False
                permission_request_error = None
                if (
                    action == "request_and_wait"
                    and getattr(e, "can_request_permission", True)
                    and not is_timeout
                ):
                    try:
                        # TelephonyProvider declares this hook and its default
                        # raises, so a provider that cannot ask for consent
                        # reports it here rather than being probed for the
                        # method and silently skipped.
                        await provider.send_call_permission_request(
                            to_number=phone_number,
                            organization_id=campaign.organization_id,
                            telephony_configuration_id=campaign.telephony_configuration_id,
                        )
                        permission_request_sent = True
                        logger.info(
                            f"[{provider.PROVIDER_NAME.upper()} Campaign] Sent permission request to {phone_number} "
                            f"for campaign {campaign.id}"
                        )
                    except Exception as req_err:
                        permission_request_error = str(req_err)
                        logger.warning(
                            f"[{provider.PROVIDER_NAME.upper()} Campaign] Failed to send permission request to {phone_number}: {req_err}"
                        )

                if permission_request_sent:
                    # Park the queued run with 24-hour expiration
                    await db_client.update_queued_run(
                        queued_run_id=queued_run.id,
                        state="queued",
                        retry_reason=retry_reason,
                        scheduled_for=datetime.now(UTC) + timedelta(hours=24),
                    )

                    # Update the existing workflow run with awaiting_permission disposition,
                    # keeping is_completed=False so it can be resumed when permission is granted
                    await db_client.update_workflow_run(
                        run_id=workflow_run.id,
                        is_completed=False,
                        state=WorkflowRunState.INITIALIZED.value,
                        gathered_context={
                            "call_disposition": TelephonyCallStatus.AWAITING_PERMISSION.value,
                            "mapped_call_disposition": TelephonyCallStatus.AWAITING_PERMISSION.value,
                            "call_status": TelephonyCallStatus.AWAITING_PERMISSION.value,
                            "error": f"Call permission requested; awaiting recipient response. {e}",
                        },
                    )
                else:
                    disposition = (
                        TelephonyCallStatus.PERMISSION_TIMEOUT.value
                        if is_timeout
                        else (
                            TelephonyCallStatus.PERMISSION_DENIED.value
                            if getattr(e, "status", None) == "denied"
                            else TelephonyCallStatus.NO_PERMISSION.value
                        )
                    )
                    await mark_workflow_run_failed(
                        workflow_run.id,
                        f"{e} Permission request could not be sent: {permission_request_error}"
                        if permission_request_error
                        else str(e),
                        disposition=disposition,
                    )
                    # Mark queued run as processed to prevent retrying
                    await db_client.update_queued_run(
                        queued_run_id=queued_run.id,
                        state="processed",
                        processed_at=datetime.now(UTC),
                    )
                    queued_run_finalized = True

                # Missing consent is the recipient's choice, not an outage, so it
                # must not trip the breaker. A permission request we could not
                # deliver is an outage, and counting it keeps a broken Meta
                # integration from silently burning through the whole campaign.
                await circuit_breaker.record_and_evaluate(
                    campaign.id,
                    is_failure=permission_request_error is not None,
                    workflow_run_id=workflow_run.id,
                    reason=(
                        "permission_request_failed"
                        if permission_request_error
                        else "whatsapp_permission_required"
                    ),
                )
                await self.release_call_slot(workflow_run.id)
                return DispatchResult(workflow_run, queued_run_finalized)

            logger.error(
                f"Failed to initiate call for workflow run {workflow_run.id}: {e}"
            )

            is_token_expired = (
                (isinstance(e, HTTPException) and e.status_code == 401)
                or "token expired" in str(e).lower()
                or "token has expired" in str(e).lower()
                or "oauth" in str(e).lower()
                or "invalid access token" in str(e).lower()
            )
            disposition = (
                TelephonyCallStatus.TOKEN_EXPIRED.value
                if is_token_expired
                else TelephonyCallStatus.FAILED.value
            )
            clean_err = (
                "WhatsApp access token has expired or is invalid. Please update credentials under Telephony Configuration."
                if is_token_expired
                else str(e)
            )

            await mark_workflow_run_failed(
                workflow_run.id,
                clean_err,
                disposition=disposition,
            )

            # Record call initiation failure in circuit breaker
            await circuit_breaker.record_and_evaluate(
                campaign.id,
                is_failure=True,
                workflow_run_id=workflow_run.id,
                reason="token_expired"
                if is_token_expired
                else "call_initiation_failed",
            )

            await self.release_call_slot(workflow_run.id)

            raise

        return DispatchResult(workflow_run, queued_run_finalized)

    async def apply_rate_limit(self, organization_id: int, rate_limit: int) -> None:
        """
        Enforces rate limiting - waits if necessary to comply with rate limit

        Example usage:
        ```
        # This will wait up to 1 second if needed to respect rate limit
        await self.apply_rate_limit(org_id, 1)  # 1 call per second
        await twilio.initiate_call(...)  # Now safe to call
        ```
        """
        max_wait = 1.0  # Maximum time to wait for a slot
        start_time = time.time()

        while True:
            # Try to acquire token
            if await rate_limiter.acquire_token(organization_id, rate_limit):
                return  # Got permission to proceed

            # Check how long to wait
            wait_time = await rate_limiter.get_next_available_slot(
                organization_id, rate_limit
            )

            # Don't wait forever
            if time.time() - start_time + wait_time > max_wait:
                raise TimeoutError("Rate limit timeout - try again later")

            # Wait for next available slot
            await asyncio.sleep(wait_time)

    async def acquire_concurrent_slot(
        self, organization_id: int, campaign: any, timeout: float = 600
    ) -> CallConcurrencySlot:
        """
        Acquires a concurrent call slot - waits if necessary until a slot is available.

        Args:
            organization_id: The organization ID
            campaign: The campaign object
            timeout: Maximum time to wait for a slot (default 10 minutes)

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

    async def acquire_from_number(
        self,
        organization_id: int,
        telephony_configuration_id: int | None,
        timeout: float = 600.0,
    ) -> Optional[str]:
        """
        Acquire a from_number from the (org, telephony config) pool with retry.
        Convenience wrapper delegating to acquire_from_number_with_token for
        backwards compatibility with existing consumers.
        """
        acquisition = await self.acquire_from_number_with_token(
            organization_id, telephony_configuration_id, timeout=timeout
        )
        return acquisition.from_number if acquisition else None

    async def acquire_from_number_with_token(
        self,
        organization_id: int,
        telephony_configuration_id: int | None,
        timeout: float = 600.0,
    ) -> Optional[FromNumberAcquisition]:
        """
        Acquires from the (org, telephony config) pool with retry, returning
        the number together with the ownership token
        (the acquisition-time score) for the acquired number. dispatch_call
        must carry this token into store_workflow_from_number_mapping and
        into any release_from_number call for this acquisition, so a release
        can never free a number that has since been re-acquired by another
        call.
        """
        wait_start = time.time()

        while True:
            acquisition = await rate_limiter.acquire_from_number_with_token(
                organization_id, telephony_configuration_id
            )
            if acquisition:
                return acquisition

            wait_time = time.time() - wait_start
            if wait_time > timeout:
                logger.warning(
                    f"From number pool exhausted for org {organization_id} "
                    f"config {telephony_configuration_id} after waiting "
                    f"{wait_time:.1f}s"
                )
                return None

            logger.debug(
                f"All from_numbers in use for org {organization_id} "
                f"config {telephony_configuration_id}, waited {wait_time:.1f}s, "
                "retrying..."
            )
            await asyncio.sleep(1)

    async def release_call_slot(self, workflow_run_id: int) -> bool:
        """
        Release concurrent slot and from_number when a call completes.
        Called by Twilio webhooks or workflow completion handlers.
        """
        slot_released = await call_concurrency.release_workflow_run_slot(
            workflow_run_id
        )

        # Release from_number back to its (org, telephony config) pool. In the
        # normal case release_workflow_run_slot above has already released
        # and deleted this mapping as part of its own cleanup; this is a
        # best-effort retry for the case where that attempt hit a Redis error
        # and deliberately kept the mapping around. Fetching the ownership
        # token (when the mapping has one) and passing it through keeps this
        # retry race-safe: it can only ever free OUR OWN acquisition, never
        # one another call has since re-acquired.
        from_number_mapping = (
            await rate_limiter.get_workflow_from_number_mapping_with_token(
                workflow_run_id
            )
        )
        if from_number_mapping:
            fn_org_id, fn_number, fn_tcid, fn_token = from_number_mapping
            fn_success = await rate_limiter.release_from_number(
                fn_org_id,
                fn_number,
                telephony_configuration_id=fn_tcid,
                expected_token=fn_token,
            )
            if fn_success:
                await rate_limiter.delete_workflow_from_number_mapping(workflow_run_id)
                logger.info(
                    f"Released from_number {fn_number} for workflow run {workflow_run_id}"
                )

        return slot_released


# Global instance
campaign_call_dispatcher = CampaignCallDispatcher()
