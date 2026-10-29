"""Dograh's lifecycle additions to Pipecat pipeline workers.

Services keep blocking I/O out of constructors and prepare through asynchronous
setup or request handling. This worker owns cancellation of that setup; the
call engine decides which operations need generation readiness.
"""

import asyncio

from pipecat.pipeline.worker import PipelineWorker, WorkerParams


class DograhPipelineWorker(PipelineWorker):
    """Cancel pending setup without waiting for an unstarted frame pipeline.

    Delegate setup and its timeout to Pipecat. The local task only makes setup
    cancellable through the worker's normal cancel path, including bus messages.
    These overrides rely on _setup_within_timeout, _cancel, and _cleanup; keep
    the lifecycle tests when upgrading Pipecat.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dograh_setup_task: asyncio.Task[bool] | None = None

    async def _cancel(self, *, reason: str | None = None):
        await super()._cancel(reason=reason)
        if self._dograh_setup_task is not None:
            self._dograh_setup_task.cancel()

    async def _setup_within_timeout(self, params: WorkerParams) -> bool:
        # The task manager is initialized inside setup, so own this task here.
        self._dograh_setup_task = asyncio.create_task(
            super()._setup_within_timeout(params), name=f"{self}::setup"
        )
        try:
            if self._cancelled:
                self._dograh_setup_task.cancel()
            return await self._dograh_setup_task and not self._cancelled
        except asyncio.CancelledError:
            if self._cancelled:
                # Pipecat's run() cleans up an unsuccessful setup.
                return False
            # External task cancellation bypasses run()'s unsuccessful-setup
            # branch, so release partially initialized processors here too.
            await self._cleanup(cleanup_pipeline=True)
            raise
        except Exception:
            # Worker-level setup failures also bypass run()'s cleanup branch.
            await self._cleanup(cleanup_pipeline=True)
            raise
        finally:
            self._dograh_setup_task = None
