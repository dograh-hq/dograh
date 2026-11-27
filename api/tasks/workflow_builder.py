"""Worker entry points and recovery for durably admitted builder requests."""

from api.db import db_client
from api.services.workflow.builder_runtime.execution import (
    enqueue_builder_run,
    execute_builder_run,
)


async def process_builder_run(ctx, run_id: str):
    await execute_builder_run(run_id)


async def sweep_builder_runs(ctx):
    for run_id in await db_client.recoverable_builder_runs():
        await enqueue_builder_run(run_id)
