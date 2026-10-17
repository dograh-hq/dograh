"""Keep websocket connection latency off the call-owned playback startup path."""

import asyncio

from pipecat.processors.frame_processor import FrameProcessorSetup


class BackgroundConnectionMixin:
    """Defer only setup's connection await, preserving upstream setup and teardown.

    For services whose setup calls ``_connect`` after local initialization.
    Session seeding and caller input still use their existing readiness gates.
    Later reconnects retain upstream's awaited behavior. The service owns the
    initial connection task and cancels it before disconnect or cleanup, so a
    late websocket cannot outlive the call.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connecting_during_setup = False
        self._startup_connection_task: asyncio.Task | None = None

    async def setup(self, setup: FrameProcessorSetup):
        self._connecting_during_setup = True
        try:
            await super().setup(setup)
        finally:
            self._connecting_during_setup = False

    async def _connect(self):
        if self._connecting_during_setup:
            if self._startup_connection_task is None:
                self._startup_connection_task = self.create_task(
                    super()._connect(), name=f"{self}::startup_connection"
                )
            return
        if (
            self._startup_connection_task is not None
            and not self._startup_connection_task.done()
        ):
            await asyncio.shield(self._startup_connection_task)
            return
        await super()._connect()

    async def _cancel_startup_connection(self):
        task = self._startup_connection_task
        if task is not None:
            await self.cancel_task(task)
            self._startup_connection_task = None

    async def _disconnect(self):
        await self._cancel_startup_connection()
        await super()._disconnect()

    async def cleanup(self):
        # Cleanup can run without a terminal frame after another processor's
        # setup fails. Some upstream services only disconnect on stop/cancel.
        await self._disconnect()
        await super().cleanup()
