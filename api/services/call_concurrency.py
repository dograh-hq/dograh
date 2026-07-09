import asyncio
import time
from dataclasses import dataclass

from loguru import logger

from api.constants import DEFAULT_ORG_CONCURRENCY_LIMIT
from api.db import db_client
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.services.campaign.rate_limiter import rate_limiter
from api.services.posthog_client import capture_event


@dataclass(frozen=True)
class CallConcurrencySlot:
    organization_id: int
    slot_id: str
    max_concurrent: int
    source: str
    scope_key: str | None = None


class CallConcurrencyLimitError(Exception):
    """Raised when an org has no available concurrent call slots."""

    def __init__(
        self,
        *,
        organization_id: int,
        source: str,
        wait_time: float,
        max_concurrent: int,
    ):
        self.organization_id = organization_id
        self.source = source
        self.wait_time = wait_time
        self.max_concurrent = max_concurrent
        super().__init__(
            f"Concurrent call limit reached for org {organization_id} "
            f"(source={source}, limit={max_concurrent}, waited={wait_time:.1f}s)"
        )


class WorkflowRunSlotAlreadyBoundError(Exception):
    """Raised when a workflow run already owns a concurrent call slot."""

    def __init__(self, workflow_run_id: int):
        self.workflow_run_id = workflow_run_id
        super().__init__(
            f"Workflow run {workflow_run_id} already has an active call slot"
        )


class CallConcurrencyService:
    def __init__(self):
        self.default_concurrent_limit = int(DEFAULT_ORG_CONCURRENCY_LIMIT)

    async def get_org_concurrent_limit(self, organization_id: int) -> int:
        """Get the concurrent call limit for an organization."""
        try:
            config = await db_client.get_configuration(
                organization_id,
                OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
            )
            if config and config.value:
                value = config.value.get("value")
                if value is not None:
                    return int(value)
        except Exception as e:
            logger.warning(
                f"Error getting concurrent limit for org {organization_id}: {e}"
            )
        return self.default_concurrent_limit

    async def acquire_org_slot(
        self,
        organization_id: int,
        *,
        source: str,
        timeout: float = 0,
        scope_key: str | None = None,
        scope_max_concurrent: int | None = None,
        retry_interval: float = 1,
    ) -> CallConcurrencySlot:
        """Acquire a slot in the org-wide concurrency counter.

        ``scope_key``/``scope_max_concurrent`` additionally bound a secondary
        counter (e.g. ``campaign:<id>``) so a source can cap its own
        concurrency without measuring — or being starved by — unrelated calls
        in the same org.
        """
        max_concurrent = await self.get_org_concurrent_limit(organization_id)
        if scope_max_concurrent is not None:
            scope_max_concurrent = int(scope_max_concurrent)

        wait_start = time.time()
        while True:
            acquisition = await rate_limiter.try_acquire_concurrent_slot_details(
                organization_id,
                max_concurrent,
                scope_key=scope_key,
                scope_max_concurrent=scope_max_concurrent,
            )
            if acquisition:
                logger.info(
                    f"Acquired concurrent call slot for org {organization_id}: "
                    f"source={source}, active_calls="
                    f"{acquisition.active_count}/{max_concurrent}, "
                    f"slot_id={acquisition.slot_id}"
                )
                return CallConcurrencySlot(
                    organization_id=organization_id,
                    slot_id=acquisition.slot_id,
                    max_concurrent=max_concurrent,
                    source=source,
                    scope_key=scope_key,
                )

            wait_time = time.time() - wait_start
            if wait_time >= timeout:
                current_count = await rate_limiter.get_concurrent_count(organization_id)
                scope_note = (
                    f", scope={scope_key} (limit={scope_max_concurrent})"
                    if scope_key
                    else ""
                )
                logger.warning(
                    f"Concurrent call limit reached for org {organization_id}: "
                    f"source={source}, active_calls={current_count}/{max_concurrent}"
                    f"{scope_note}, waited={wait_time:.1f}s"
                )
                properties = {
                    "event_source": "dograh",
                    "organization_id": organization_id,
                    "source": source,
                    "max_concurrent": max_concurrent,
                    "active_calls": current_count,
                    "waited_seconds": round(wait_time, 1),
                }
                if scope_key:
                    properties["scope_key"] = scope_key
                    properties["scope_max_concurrent"] = scope_max_concurrent
                await self._notify_limit_reached(organization_id, properties)
                raise CallConcurrencyLimitError(
                    organization_id=organization_id,
                    source=source,
                    wait_time=wait_time,
                    max_concurrent=max_concurrent,
                )

            logger.debug(
                f"Waiting for concurrent call slot for org {organization_id}, "
                f"source={source}, waited {wait_time:.1f}s"
            )
            await asyncio.sleep(min(retry_interval, max(0, timeout - wait_time)))

    async def _notify_limit_reached(
        self, organization_id: int, properties: dict
    ) -> None:
        """Fan the usage event out to every org member's provider_id, matching
        how MPS emits org-scoped billing events (billing_posthog_service.py)
        into the shared PostHog project. Never raises.

        NOTE: intentionally NOT attaching ``$groups`` (organization) to this
        event. PostHog evaluates a $groups event at both person and group
        scope, which double-triggers person-scoped workflows enrolled on the
        event. The org is still available as the ``organization_id`` property.
        """
        try:
            members = await db_client.get_organization_users(organization_id)
            if not members:
                logger.debug(
                    f"No users found for org {organization_id}; skipping "
                    "concurrent-call-limit PostHog event"
                )
                return
            for member in members:
                capture_event(
                    distinct_id=str(member.provider_id),
                    event=PostHogEvent.USAGE_CONCURRENT_CALL_LIMIT_REACHED,
                    properties=properties,
                )
        except Exception:
            logger.exception(
                "Failed to send concurrent-call-limit PostHog event for org "
                f"{organization_id}"
            )

    async def bind_workflow_run(
        self, slot: CallConcurrencySlot, workflow_run_id: int
    ) -> None:
        stored = await rate_limiter.store_workflow_slot_mapping_if_absent(
            workflow_run_id,
            slot.organization_id,
            slot.slot_id,
            scope_key=slot.scope_key,
        )
        if stored:
            return

        await self.release_slot(slot)
        raise WorkflowRunSlotAlreadyBoundError(workflow_run_id)

    async def register_active_call(
        self,
        organization_id: int,
        workflow_run_id: int,
        *,
        source: str,
        timeout: float = 0,
        scope_key: str | None = None,
        scope_max_concurrent: int | None = None,
        retry_interval: float = 1,
    ) -> CallConcurrencySlot:
        slot = await self.acquire_org_slot(
            organization_id,
            source=source,
            timeout=timeout,
            scope_key=scope_key,
            scope_max_concurrent=scope_max_concurrent,
            retry_interval=retry_interval,
        )
        await self.bind_workflow_run(slot, workflow_run_id)
        return slot

    async def unregister_active_call(self, workflow_run_id: int) -> bool:
        """Release the run's slot without ever raising.

        Callers invoke this from ``finally`` blocks during pipeline/socket
        teardown; a cleanup failure must not mask the original exception.
        The slot mapping survives a failed release, so a later cleanup path
        (status callback, StasisEnd) or the Redis stale timeout recovers it.
        """
        try:
            return await self.release_workflow_run_slot(workflow_run_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Failed to release concurrent call slot for workflow run "
                f"{workflow_run_id}: {e}"
            )
            return False

    async def release_slot(self, slot: CallConcurrencySlot | None) -> bool:
        if slot is None:
            return False
        released = await rate_limiter.release_concurrent_slot(
            slot.organization_id, slot.slot_id, scope_key=slot.scope_key
        )
        return bool(released)

    async def release_workflow_run_slot(self, workflow_run_id: int) -> bool:
        mapping = await rate_limiter.get_workflow_slot_mapping(workflow_run_id)
        if not mapping:
            return False

        org_id, slot_id, scope_key = mapping
        released = await rate_limiter.release_concurrent_slot(
            org_id, slot_id, scope_key=scope_key
        )
        if released is None:
            # Redis error while releasing — keep the mapping so a later
            # cleanup path can retry instead of orphaning a live slot until
            # the stale timeout.
            logger.warning(
                f"Failed to release concurrent slot for workflow run "
                f"{workflow_run_id}; keeping mapping for retry"
            )
            return False
        await rate_limiter.delete_workflow_slot_mapping(workflow_run_id)
        if released:
            logger.info(f"Released concurrent slot for workflow run {workflow_run_id}")
        else:
            logger.debug(
                f"Concurrent slot mapping for workflow run {workflow_run_id} "
                "had no live slot; deleted stale mapping"
            )
        return released


call_concurrency = CallConcurrencyService()
