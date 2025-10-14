import asyncio
import time
from datetime import UTC, datetime
from typing import Optional

from loguru import logger

from api.db import db_client
from api.db.models import QueuedRunModel, WorkflowRunModel
from api.enums import OrganizationConfigurationKey, WorkflowRunMode
from api.services.campaign.rate_limiter import rate_limiter
from api.services.telephony.factory import get_telephony_provider
from api.services.telephony.base import TelephonyProvider
from api.utils.tunnel import TunnelURLProvider


class CampaignCallDispatcher:
    """Manages rate-limited and concurrent-limited call dispatching"""

    def __init__(self):
        self.default_concurrent_limit = 20

    async def get_telephony_provider(self, organization_id: int) -> TelephonyProvider:
        """Get telephony provider instance for specific organization"""
        return await get_telephony_provider(organization_id)

    async def get_org_concurrent_limit(self, organization_id: int) -> int:
        """Get the concurrent call limit for an organization."""
        try:
            config = await db_client.get_configuration(
                organization_id,
                OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
            )
            if config and config.value:
                return int(config.value["value"])
        except Exception as e:
            logger.warning(
                f"Error getting concurrent limit for org {organization_id}: {e}"
            )
        return self.default_concurrent_limit

    async def process_batch(self, campaign_id: int, batch_size: int = 10) -> int:
        """
        Processes a batch of queued runs with priority for scheduled retries
        Returns: number of processed runs
        """
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

        # First, get any scheduled retries that are due
        scheduled_runs = await db_client.get_scheduled_queued_runs(
            campaign_id=campaign_id,
            scheduled_before=datetime.now(UTC),
            limit=batch_size,
        )

        remaining_slots = batch_size - len(scheduled_runs)

        # Then get regular queued runs
        regular_runs = []
        if remaining_slots > 0:
            regular_runs = await db_client.get_queued_runs(
                campaign_id=campaign_id,
                state="queued",
                scheduled_for=False,  # Exclude scheduled runs
                limit=remaining_slots,
            )

        queued_runs = scheduled_runs + regular_runs

        if not queued_runs:
            logger.info(f"No more queued runs for campaign {campaign_id}")
            return 0

        processed_count = 0
        for queued_run in queued_runs:
            try:
                # Apply rate limiting
                await self.apply_rate_limit(
                    campaign.organization_id, campaign.rate_limit_per_second
                )

                # Dispatch the call
                workflow_run = await self.dispatch_call(queued_run, campaign)

                # Update queued run as processed
                await db_client.update_queued_run(
                    queued_run_id=queued_run.id,
                    state="processed",
                    workflow_run_id=workflow_run.id,
                    processed_at=datetime.now(UTC),
                )

                processed_count += 1

                # Update campaign processed count
                await db_client.update_campaign(
                    campaign_id=campaign_id, processed_rows=campaign.processed_rows + 1
                )

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

        return processed_count

    async def dispatch_call(
        self, queued_run: QueuedRunModel, campaign: any
    ) -> Optional[WorkflowRunModel]:
        """Creates workflow run and initiates call with concurrent limiting"""
        # Get concurrent limit for organization
        max_concurrent = await self.get_org_concurrent_limit(campaign.organization_id)

        # Track wait time for alerting
        wait_start = time.time()
        slot_id = None

        # Wait until we can acquire a concurrent slot
        while True:
            slot_id = await rate_limiter.try_acquire_concurrent_slot(
                campaign.organization_id, max_concurrent
            )
            if slot_id:
                break

            # Check if we've been waiting too long
            wait_time = time.time() - wait_start
            if wait_time > 600:  # 10 minutes
                logger.error(
                    f"Waiting for concurrent slot for {wait_time:.1f}s, "
                    f"org: {campaign.organization_id}, campaign: {campaign.id}"
                )

            logger.debug(
                f"Attempting to get a slot for {campaign.organization_id} {campaign.id}"
            )

            # Wait before retrying
            await asyncio.sleep(1)

        # Get workflow details
        workflow = await db_client.get_workflow_by_id(campaign.workflow_id)
        if not workflow:
            # Release slot before raising
            await rate_limiter.release_concurrent_slot(
                campaign.organization_id, slot_id
            )
            raise ValueError(f"Workflow {campaign.workflow_id} not found")

        # Merge context variables (queued_run context already includes retry info if applicable)
        initial_context = {
            **workflow.template_context_variables,
            **queued_run.context_variables,
            "campaign_id": campaign.id,
        }

        # Extract phone number
        phone_number = queued_run.context_variables.get("phone_number")
        if not phone_number:
            # Release slot before raising
            await rate_limiter.release_concurrent_slot(
                campaign.organization_id, slot_id
            )
            raise ValueError(f"No phone number in queued run {queued_run.id}")

        # Create workflow run with queued_run_id tracking
        workflow_run_name = f"WR-CAMPAIGN-{campaign.id}-{queued_run.id}"

        try:
            workflow_run = await db_client.create_workflow_run(
                name=workflow_run_name,
                workflow_id=campaign.workflow_id,
                mode=WorkflowRunMode.TWILIO.value,
                user_id=campaign.created_by,
                initial_context=initial_context,
                campaign_id=campaign.id,
                queued_run_id=queued_run.id,  # Link to queued run for retry tracking
            )

            # Store slot_id mapping in Redis for cleanup later
            await rate_limiter.store_workflow_slot_mapping(
                workflow_run.id, campaign.organization_id, slot_id
            )
        except Exception as e:
            # Release slot on error
            await rate_limiter.release_concurrent_slot(
                campaign.organization_id, slot_id
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

        # Initiate call via telephony provider
        try:
            provider = await self.get_telephony_provider(campaign.organization_id)
            
            # Construct webhook URL with parameters
            backend_endpoint = await TunnelURLProvider.get_tunnel_url()
            webhook_url = (
                f"https://{backend_endpoint}/api/v1/telephony/twiml"
                f"?workflow_id={campaign.workflow_id}"
                f"&user_id={campaign.created_by}"
                f"&workflow_run_id={workflow_run.id}"
                f"&campaign_id={campaign.id}"
                f"&organization_id={campaign.organization_id}"
            )
            
            call_result = await provider.initiate_call(
                to_number=phone_number,
                webhook_url=webhook_url,
                workflow_run_id=workflow_run.id,
            )

            logger.info(
                f"Call initiated for workflow run {workflow_run.id}, SID: {call_result.get('sid')}"
            )

        except Exception as e:
            logger.error(
                f"Failed to initiate call for workflow run {workflow_run.id}: {e}"
            )

            # Update workflow run as failed
            twilio_callback_logs = workflow_run.logs.get("twilio_status_callbacks", [])
            twilio_callback_log = {
                "status": "failed",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {"error": str(e)},
            }
            twilio_callback_logs.append(twilio_callback_log)
            await db_client.update_workflow_run(
                run_id=workflow_run.id,
                is_completed=True,
                gathered_context={
                    "error": str(e),
                },
                logs={
                    "twilio_status_callbacks": twilio_callback_logs,
                },
            )

            # Release concurrent slot on failure
            mapping = await rate_limiter.get_workflow_slot_mapping(workflow_run.id)
            if mapping:
                org_id, slot_id = mapping
                await rate_limiter.release_concurrent_slot(org_id, slot_id)
                await rate_limiter.delete_workflow_slot_mapping(workflow_run.id)

            raise

        return workflow_run

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

    async def release_call_slot(self, workflow_run_id: int) -> bool:
        """
        Release concurrent slot when a call completes.
        Called by Twilio webhooks or workflow completion handlers.
        """
        mapping = await rate_limiter.get_workflow_slot_mapping(workflow_run_id)
        if mapping:
            org_id, slot_id = mapping
            success = await rate_limiter.release_concurrent_slot(org_id, slot_id)
            if success:
                await rate_limiter.delete_workflow_slot_mapping(workflow_run_id)
                logger.info(
                    f"Released concurrent slot for workflow run {workflow_run_id}"
                )
            return success
        return False


# Global instance
campaign_call_dispatcher = CampaignCallDispatcher()
