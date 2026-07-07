import asyncio
import time
from dataclasses import dataclass

from loguru import logger

from api.constants import DEFAULT_ORG_CONCURRENCY_LIMIT
from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.campaign.rate_limiter import rate_limiter


@dataclass(frozen=True)
class CallConcurrencySlot:
    organization_id: int
    slot_id: str
    max_concurrent: int
    source: str


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
        max_concurrent_override: int | None = None,
        retry_interval: float = 1,
    ) -> CallConcurrencySlot:
        org_concurrent_limit = await self.get_org_concurrent_limit(organization_id)
        if max_concurrent_override is None:
            max_concurrent = org_concurrent_limit
        else:
            max_concurrent = min(int(max_concurrent_override), org_concurrent_limit)

        wait_start = time.time()
        while True:
            acquisition = await rate_limiter.try_acquire_concurrent_slot_details(
                organization_id, max_concurrent
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
                )

            wait_time = time.time() - wait_start
            if wait_time >= timeout:
                current_count = await rate_limiter.get_concurrent_count(organization_id)
                logger.warning(
                    f"Concurrent call limit reached for org {organization_id}: "
                    f"source={source}, active_calls={current_count}/{max_concurrent}, "
                    f"waited={wait_time:.1f}s"
                )
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

    async def bind_workflow_run(
        self, slot: CallConcurrencySlot, workflow_run_id: int
    ) -> None:
        stored = await rate_limiter.store_workflow_slot_mapping_if_absent(
            workflow_run_id,
            slot.organization_id,
            slot.slot_id,
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
        max_concurrent_override: int | None = None,
        retry_interval: float = 1,
    ) -> CallConcurrencySlot:
        slot = await self.acquire_org_slot(
            organization_id,
            source=source,
            timeout=timeout,
            max_concurrent_override=max_concurrent_override,
            retry_interval=retry_interval,
        )
        await self.bind_workflow_run(slot, workflow_run_id)
        return slot

    async def unregister_active_call(self, workflow_run_id: int) -> bool:
        return await self.release_workflow_run_slot(workflow_run_id)

    async def release_slot(self, slot: CallConcurrencySlot | None) -> bool:
        if slot is None:
            return False
        return await rate_limiter.release_concurrent_slot(
            slot.organization_id, slot.slot_id
        )

    async def release_workflow_run_slot(self, workflow_run_id: int) -> bool:
        mapping = await rate_limiter.get_workflow_slot_mapping(workflow_run_id)
        if not mapping:
            return False

        org_id, slot_id = mapping
        released = await rate_limiter.release_concurrent_slot(org_id, slot_id)
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
