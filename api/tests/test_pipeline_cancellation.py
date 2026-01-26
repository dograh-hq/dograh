import asyncio

import pytest
from loguru import logger

from pipecat.frames.frames import (
    EndTaskFrame,
    Frame,
    InterruptionTaskFrame,
    LLMRunFrame,
)
from pipecat.pipeline.base_task import PipelineTaskParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class MockTransport(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class BusyWaitProcessor(FrameProcessor):
    def __init__(self, wait_time=5.0, **kwargs):
        super().__init__(**kwargs)
        self._wait_time = wait_time

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMRunFrame):
            # Simulate a delay, which can happen sometimes due to slow LLM Inferencing or
            # other reasons
            try:
                logger.debug(f"{self} sleeping with frame: {frame}")
                await asyncio.sleep(5)
                logger.debug(f"{self} woke up with frame: {frame}")
            except asyncio.CancelledError:
                logger.debug(f"{self} was cancelled")
                raise
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_interruption_with_blocked_end_frame():
    busy_wait_processor = BusyWaitProcessor(wait_time=5)
    transport = MockTransport()
    pipeline = Pipeline([transport, busy_wait_processor])

    task = PipelineTask(pipeline, enable_rtvi=False)

    async def run_pipeline():
        loop = asyncio.get_running_loop()
        params = PipelineTaskParams(loop=loop)
        await task.run(params=params)

    async def queue_frame():
        await task.queue_frames([LLMRunFrame()])

        # Send EndTaskFrame to simulate EndFrame
        await asyncio.sleep(0.1)
        await transport.queue_frame(EndTaskFrame(), direction=FrameDirection.UPSTREAM)

        # Simulate an Interruption, which can happen if the user
        # has started to speak
        await asyncio.sleep(0.1)
        await transport.queue_frame(
            InterruptionTaskFrame(), direction=FrameDirection.UPSTREAM
        )

    # Create tasks explicitly for better control
    pipeline_task = asyncio.create_task(run_pipeline())
    queue_task = asyncio.create_task(queue_frame())

    # Wait with timeout
    done, pending = await asyncio.wait(
        [pipeline_task, queue_task],
        timeout=1.0,
        return_when=asyncio.ALL_COMPLETED,
    )

    # If there are pending tasks, we timed out
    if pending:
        # Cancel all pending tasks
        for t in pending:
            t.cancel()

        # Give limited time for cleanup, then move on regardless
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass  # Cleanup took too long, continue anyway

        pytest.fail("Test timed out after 1 second")
