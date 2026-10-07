import asyncio

from pipecat.pipeline.worker import PipelineWorker
from pipecat.workers.runner import WorkerRunner


def create_worker_runner(
    *,
    handle_sigint: bool = False,
    handle_sigterm: bool = False,
) -> WorkerRunner:
    """Create the runner that owns a call's worker bus and registry.

    Agent transfer needs the bus before the pipeline is built, because the
    call pipeline's bridge is constructed with it, so the runner is created up
    front and handed to :func:`run_worker_runner` once everything is wired.
    """
    return WorkerRunner(handle_sigint=handle_sigint, handle_sigterm=handle_sigterm)


async def run_worker_runner(
    runner: WorkerRunner,
    worker: PipelineWorker,
    *,
    auto_end: bool = True,
) -> None:
    """Run ``worker`` as the runner's root worker until it finishes.

    Agent workers added later are children of ``worker``: they carry a parent,
    so they do not hold the runner up once the call pipeline has finished.
    """
    await runner.add_workers(worker)
    await runner.run(auto_end=auto_end)


async def run_pipeline_worker(
    worker: PipelineWorker,
    *,
    handle_sigint: bool = False,
    handle_sigterm: bool = False,
    auto_end: bool = True,
) -> None:
    """Run a pipeline worker through the v1.3 worker runner lifecycle."""
    runner = create_worker_runner(
        handle_sigint=handle_sigint, handle_sigterm=handle_sigterm
    )
    await run_worker_runner(runner, worker, auto_end=auto_end)


async def wait_for_pipeline_worker_started(
    worker: PipelineWorker,
    *,
    timeout: float = 3.0,
    run_task: asyncio.Task | None = None,
) -> None:
    """Wait until a pipeline worker has fired its stable start lifecycle."""

    async def _wait_until_started():
        while worker.started_at is None:
            if run_task and run_task.done():
                await run_task
            if worker.has_finished():
                raise RuntimeError("PipelineWorker finished before starting")
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_until_started(), timeout=timeout)
