"""A pipeline that wants to stop asks the engine to end the call instead.

Cancelling the pipeline directly races the engine's teardown: whichever
finishes first decides what ``on_pipeline_finished`` snapshots and ships to the
customer's dialer, and over 238 measured hangups the pipeline won 24 times,
persisting the placeholder disposition instead of the extracted outcome. These
cover the interception that turns that race into an ordering, and the escape
hatches that keep a pipeline from living forever when the engine cannot finish.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    CancelWorkerFrame,
    ErrorFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests import run_test
from pipecat.utils.enums import EndTaskReason

from api.services.pipecat import termination_funnel_processor as funnel_module
from api.services.pipecat.pipeline_builder import (
    build_pipeline,
    build_realtime_pipeline,
)
from api.services.pipecat.termination_funnel_processor import (
    TerminationFunnelProcessor,
)


def _recording_funnel(*, register=True):
    """A funnel whose handler records what it was asked to dispose of."""
    calls = []
    handled = asyncio.Event()

    async def handler(reason, error):
        calls.append((reason, error))
        handled.set()

    processor = TerminationFunnelProcessor()
    if register:
        processor.set_termination_handler(handler)
    return processor, calls, handled


async def _send_upstream(processor, frames):
    _, up = await run_test(
        processor,
        frames_to_send=frames,
        frames_to_send_direction=FrameDirection.UPSTREAM,
    )
    return up


# --------------------------------------------------------------------------
# What gets intercepted
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cancelled_pipeline_is_handed_to_the_engine():
    # The output transport pushes this after it gives up writing audio. Left
    # alone it reaches the worker, which cancels the pipeline out from under
    # whatever teardown is already running.
    processor, calls, handled = _recording_funnel()

    up = await _send_upstream(
        processor, [CancelWorkerFrame(reason="audio_output_write_failed")]
    )

    await asyncio.wait_for(handled.wait(), timeout=2)
    assert calls == [(EndTaskReason.SYSTEM_CANCELLED.value, None)]
    assert not [f for f in up if isinstance(f, CancelWorkerFrame)]


@pytest.mark.asyncio
async def test_a_cancel_that_names_an_outcome_keeps_it():
    processor, calls, handled = _recording_funnel()

    await _send_upstream(
        processor, [CancelWorkerFrame(reason=EndTaskReason.USER_HANGUP.value)]
    )

    await asyncio.wait_for(handled.wait(), timeout=2)
    assert calls[0][0] == EndTaskReason.USER_HANGUP.value


@pytest.mark.asyncio
async def test_a_fatal_error_is_disposed_of_rather_than_cancelled():
    # The worker turns a fatal ErrorFrame into a CancelFrame of its own, which
    # is the same race by another name.
    processor, calls, handled = _recording_funnel()
    error = ErrorFrame("unrecoverable failure", fatal=True)

    up = await _send_upstream(processor, [error])

    await asyncio.wait_for(handled.wait(), timeout=2)
    assert calls == [(EndTaskReason.PIPELINE_ERROR.value, error)]
    assert not [f for f in up if isinstance(f, ErrorFrame)]


@pytest.mark.asyncio
async def test_a_recoverable_error_is_left_alone():
    # Reconnect and retry paths emit these constantly; they are not the end of
    # the call and the worker's own handler still wants to see them.
    processor, calls, _ = _recording_funnel()

    up = await _send_upstream(processor, [ErrorFrame("provider reconnect")])

    assert calls == []
    assert [f for f in up if isinstance(f, ErrorFrame)]


@pytest.mark.asyncio
async def test_ordinary_upstream_frames_pass_through():
    processor, calls, _ = _recording_funnel()

    up = await _send_upstream(processor, [TextFrame("hello")])

    assert calls == []
    assert [f for f in up if isinstance(f, TextFrame)]


# --------------------------------------------------------------------------
# Never swallow the only way out
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_handler_the_pipeline_ends_the_way_it_always_did():
    # The handler is registered after the pipeline is built. Until it is, and
    # if wiring is ever missed, cancelling must still work.
    processor, calls, _ = _recording_funnel(register=False)

    up = await _send_upstream(processor, [CancelWorkerFrame()])

    assert calls == []
    assert [f for f in up if isinstance(f, CancelWorkerFrame)]


@pytest.mark.asyncio
async def test_a_second_cancellation_is_not_deferred():
    # Something asked twice: the engine has had its turn and is not making
    # progress. Escalate rather than swallow the request again.
    processor, _, handled = _recording_funnel()

    up = await _send_upstream(processor, [CancelWorkerFrame(), CancelWorkerFrame()])

    await asyncio.wait_for(handled.wait(), timeout=2)
    assert len([f for f in up if isinstance(f, CancelWorkerFrame)]) == 1


@pytest.mark.asyncio
async def test_a_teardown_that_never_returns_is_cancelled_anyway(monkeypatch):
    # Deferring termination means nothing else will end this pipeline, so a
    # teardown that hangs would leak the worker and the telephony channel
    # behind it.
    monkeypatch.setattr(funnel_module, "TERMINATION_GRACE_SECONDS", 0.05)
    processor = TerminationFunnelProcessor()
    pushed = []

    async def never_returns(reason, error):
        await asyncio.sleep(60)

    async def capture(frame, direction):
        pushed.append((frame, direction))

    processor.set_termination_handler(never_returns)
    monkeypatch.setattr(processor, "push_frame", capture)

    await processor._terminate(EndTaskReason.USER_HANGUP.value, None)

    [(frame, direction)] = pushed
    assert isinstance(frame, CancelWorkerFrame)
    assert direction == FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_a_teardown_that_never_queues_a_terminal_frame_is_cancelled_anyway(
    monkeypatch,
):
    # `end_call_with_reason` returns immediately when another path is already
    # disposing of the call, so a handler returning is not proof the pipeline
    # is stopping.
    monkeypatch.setattr(funnel_module, "TERMINATION_GRACE_SECONDS", 0.05)
    processor = TerminationFunnelProcessor()
    pushed = []

    async def returns_immediately(reason, error):
        return None

    async def capture(frame, direction):
        pushed.append((frame, direction))

    processor.set_termination_handler(returns_immediately)
    monkeypatch.setattr(processor, "push_frame", capture)

    await processor._terminate(EndTaskReason.USER_HANGUP.value, None)

    assert [type(frame) for frame, _ in pushed] == [CancelWorkerFrame]


@pytest.mark.asyncio
async def test_a_terminal_frame_going_downstream_settles_the_wait():
    # This is how the funnel learns the engine got there: the frame
    # `end_call_with_reason` queues passes through it on its way down.
    processor, _, _ = _recording_funnel()

    await _send_upstream(processor, [TextFrame("hello")])

    assert processor._pipeline_ending.is_set()


# --------------------------------------------------------------------------
# Where it has to sit
# --------------------------------------------------------------------------


class TestPipelinePosition:
    """Upstream frames only pass processors between their source and the head.

    The output transport pushes its cancellation upstream, so a funnel placed
    anywhere below the input transport would never see it. Any processor added
    ahead of it in future would be equally invisible.
    """

    def _transport(self):
        input_processor = FrameProcessor()
        output_processor = FrameProcessor()
        return SimpleNamespace(
            input=lambda: input_processor,
            output=lambda: output_processor,
        )

    def test_the_funnel_sits_directly_behind_the_input_transport(self):
        transport = self._transport()
        funnel = TerminationFunnelProcessor()

        pipeline = build_pipeline(
            transport,
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            funnel,
        )

        processors = pipeline.processors
        assert processors.index(funnel) == processors.index(transport.input()) + 1

    def test_the_realtime_pipeline_places_it_the_same_way(self):
        transport = self._transport()
        funnel = TerminationFunnelProcessor()

        pipeline = build_realtime_pipeline(
            transport,
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            FrameProcessor(),
            funnel,
        )

        processors = pipeline.processors
        assert processors.index(funnel) == processors.index(transport.input()) + 1
