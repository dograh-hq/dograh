"""Dograh's delayed race between two unchanged Pipecat Gemini services.

The children run in Pipecat's direct mode, in managed per-request tasks. Their
output boundaries never wait for downstream delivery: a synchronous decision
precedes tool execution, and a stalled consumer cannot strand an SDK iterator
between reads. The outer processor owns frame ordering and flush barriers.
"""

import asyncio
import time
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    FunctionCallsFromLLMInfoFrame,
    LLMContextFrame,
    LLMContextSummaryRequestFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
    StartFrame,
)
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    TTFATMetricsData,
    TTFBMetricsData,
)
from pipecat.pipeline.pipeline import PipelineSink, PipelineSource
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.services.llm_service import FunctionCallRunnerItem, LLMService
from pipecat.services.settings import LLMSettings
from pipecat.utils.types import NOT_GIVEN


@dataclass
class _Attempt:
    index: int
    race: "_Race"
    task: asyncio.Task | None = None
    deadline: asyncio.Timeout | None = None
    error: ErrorFrame | None = None
    done: bool = False
    first_output: float | None = None
    buffered: list[tuple[Frame, FrameDirection]] = field(default_factory=list)


@dataclass
class _Race:
    started: float = field(default_factory=time.monotonic)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    attempts: list[_Attempt] = field(default_factory=list)
    winner: _Attempt | None = None
    interrupted: bool = False
    superseded: bool = False


_attempt: ContextVar[_Attempt | None] = ContextVar("fallback_attempt", default=None)
_input_id: ContextVar[int | None] = ContextVar("fallback_input_id", default=None)


class FallbackLLMProcessor(LLMService):
    """Race Gemini services without changing their request or tool implementations.

    Both children must be constructed with ``enable_direct_mode=True``. TTFB is
    an internal arbitration signal: Gemini emits it on the first candidates,
    including tool-only and signature-only responses. It is collected even when
    public metrics are disabled. Other provider families need their own audit
    of this signal and the pre-tool output boundary before being enabled here.
    """

    def __init__(
        self,
        primary: LLMService,
        fallback: LLMService,
        *,
        fallback_after_secs: float,
        first_output_timeout_secs: float = 20,
    ):
        if not all(service._enable_direct_mode for service in (primary, fallback)):
            raise ValueError("Fallback children must use enable_direct_mode=True")
        super().__init__(settings=primary._settings)
        self.primary = primary
        self.fallback = fallback
        self._services = [primary, fallback]
        self._fallback_after_secs = fallback_after_secs
        self._first_output_timeout_secs = first_output_timeout_secs
        self._fallback_metrics = {"started": 0, "won": 0}
        self._latest: list[_Attempt | None] = [None, None]
        self._outbox: asyncio.Queue[
            tuple[Frame, FrameDirection, _Attempt | None] | asyncio.Future[None]
        ] = asyncio.Queue()
        self._output_task: asyncio.Task | None = None
        self._boundaries: list[FrameProcessor] = []
        for index, service in enumerate(self._services):
            receive = partial(self._receive, index)
            source = PipelineSource(receive)
            sink = PipelineSink(receive)
            source.link(service)
            service.link(sink)
            self._boundaries.extend([source, sink])
            service.add_event_handler("on_function_calls_prepared", self._prepare_tools)

    @property
    def fallback_metrics(self) -> dict[str, int]:
        return dict(self._fallback_metrics)

    @property
    def processors(self) -> list[FrameProcessor]:
        return [*self._services, *self._boundaries]

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)
        # Only accepted output is observed externally. Internal TTFB must remain
        # enabled on every generation, independently of reporting preferences.
        internal = replace(
            setup, enable_metrics=True, report_only_initial_ttfb=False, observer=None
        )
        for processor in self.processors:
            await processor.setup(internal)

    async def cleanup(self) -> None:
        await super().cleanup()
        for processor in self.processors:
            await processor.cleanup()
        if self._output_task:
            await self.cancel_task(self._output_task)
            self._output_task = None

    def get_llm_adapter(self):
        return self.primary.get_llm_adapter()

    def register_function(self, function_name, handler, **kwargs):
        for service in self._services:
            service.register_function(function_name, handler, **kwargs)

    def unregister_function(self, function_name):
        for service in self._services:
            service.unregister_function(function_name)

    async def run_inference(self, context, **kwargs):
        return await self.primary.run_inference(context, **kwargs)

    async def _update_settings(self, delta: LLMSettings) -> dict[str, Any]:
        changed = await self.primary._update_settings(delta)
        # System instructions and sampling updates reach both services. An
        # update to the primary model must not erase the configured backup.
        await self.fallback._update_settings(replace(delta, model=NOT_GIVEN))
        return changed

    async def _prepare_tools(
        self, service: LLMService, items: Sequence[FunctionCallRunnerItem]
    ) -> None:
        attempt = _attempt.get()
        if (
            attempt is None
            or attempt.race.winner is not attempt
            or attempt.race.interrupted
            or attempt.race.superseded
        ):
            # BaseObject catches Exception, not CancelledError. This stops the
            # runner before Dograh's hook can accept independently owned work.
            raise asyncio.CancelledError
        await self._call_event_handler("on_function_calls_prepared", items)

    def _emit(
        self, frame: Frame, direction: FrameDirection, attempt: _Attempt | None = None
    ) -> None:
        self._outbox.put_nowait((frame, direction, attempt))

    async def _drain_output(self) -> None:
        while True:
            item = await self._outbox.get()
            if isinstance(item, asyncio.Future):
                if not item.done():
                    item.set_result(None)
                continue
            frame, direction, attempt = item
            # A barge-in can overtake buffered interruptible response frames.
            if (
                attempt
                and (attempt.race.interrupted or attempt.race.superseded)
                and frame.interruptible
            ):
                continue
            await FrameProcessor.push_frame(self, frame, direction)

    async def _flush_output(self) -> None:
        barrier: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._outbox.put_nowait(barrier)
        await barrier

    async def _receive(
        self, index: int, frame: Frame, direction: FrameDirection
    ) -> None:
        if frame.id == _input_id.get():
            return
        attempt = _attempt.get()
        if attempt is None:
            # Startup metadata/summary output has one owner.
            if index == 0:
                self._emit(frame, direction)
            return
        race = attempt.race
        if race.interrupted and asyncio.current_task() is attempt.task:
            return
        if isinstance(frame, ErrorFrame):
            attempt.error = frame
            race.changed.set()
            if race.winner is not attempt:
                return
        if isinstance(frame, MetricsFrame):
            if (
                any(isinstance(data, TTFBMetricsData) for data in frame.data)
                and attempt.error is None
                and race.winner is None
                and not race.interrupted
                and asyncio.current_task() is attempt.task
            ):
                attempt.first_output = time.monotonic() - race.started
                race.winner = attempt
                if attempt.deadline:
                    attempt.deadline.reschedule(None)
                if index == 1:
                    self._fallback_metrics["won"] += 1
                for buffered, buffered_direction in attempt.buffered:
                    self._emit(buffered, buffered_direction, attempt)
                attempt.buffered.clear()
                race.changed.set()
            # Include the delay before a backup request in user-visible timing.
            for data in frame.data:
                if isinstance(data, TTFBMetricsData):
                    data.value = time.monotonic() - race.started
                elif isinstance(data, TTFATMetricsData):
                    data.ttfat = time.monotonic() - race.started
                    data.ttfb = attempt.first_output or data.ttfb
                    data.thinking_time = data.ttfat - data.ttfb
            frame.data = [
                data
                for data in frame.data
                if (
                    self.usage_metrics_enabled
                    if isinstance(data, LLMUsageMetricsData)
                    else self.metrics_enabled
                )
            ]
            if not frame.data:
                return
        if race.winner is attempt:
            self._emit(frame, direction, attempt)
        elif isinstance(frame, FunctionCallsFromLLMInfoFrame):
            # Gemini emits this synchronously before run_function_calls, which
            # can itself perform workflow transitions before scheduling tools.
            raise asyncio.CancelledError
        elif race.winner is None and isinstance(frame, LLMFullResponseStartFrame):
            attempt.buffered.append((frame, direction))

    async def _run_attempt(self, attempt: _Attempt, frame: LLMContextFrame) -> None:
        token = _attempt.set(attempt)
        try:
            async with asyncio.timeout(self._first_output_timeout_secs) as deadline:
                attempt.deadline = deadline
                await self._services[attempt.index].queue_frame(frame)
        except Exception as exc:  # noqa: BLE001 - managed tasks otherwise swallow failures
            attempt.error = ErrorFrame(
                error="LLM completion timeout"
                if isinstance(exc, TimeoutError)
                else str(exc),
                exception=exc,
                processor=self,
            )
            if attempt.race.winner is attempt:
                self._emit(attempt.error, FrameDirection.UPSTREAM, attempt)
        finally:
            attempt.done = True
            if attempt.race.winner is None and attempt.error is None:
                attempt.error = ErrorFrame(
                    error="Gemini completed without candidates", processor=self
                )
            attempt.race.changed.set()
            _attempt.reset(token)

    def _launch(self, race: _Race, index: int, frame: LLMContextFrame) -> None:
        attempt = _Attempt(index=index, race=race)
        race.attempts.append(attempt)
        self._latest[index] = attempt
        if index == 1:
            self._fallback_metrics["started"] += 1
        attempt.task = self.create_task(self._run_attempt(attempt, frame))

    async def _cancel_attempts(
        self, race: _Race, *, except_winner: bool = False
    ) -> None:
        await asyncio.gather(
            *(
                self.cancel_task(attempt.task)
                for attempt in race.attempts
                if attempt.task
                and not attempt.task.done()
                and not (except_winner and attempt is race.winner)
            )
        )

    async def _finish_race(
        self, race: _Race, loser_cleanup: asyncio.Task | None
    ) -> None:
        if loser_cleanup:
            await loser_cleanup
        await self._cancel_attempts(race)
        await self._flush_output()

    async def _generate(self, frame: LLMContextFrame) -> None:
        for index, service in enumerate(self._services):
            previous = self._latest[index]
            if previous:
                previous.race.superseded = True
            # A previously winning backup might not receive this new context.
            # Its deferred transition must still be retired just as it would
            # be by LLMService._begin_tool_response on a single service.
            service._discard_pending_node_transition_calls("new_context")
        race = _Race()
        self._launch(race, 0, frame)
        loser_cleanup = None
        try:
            while race.winner is None:
                race.changed.clear()
                primary = race.attempts[0]
                remaining = self._fallback_after_secs - (
                    time.monotonic() - race.started
                )
                if len(race.attempts) == 1 and (
                    remaining <= 0 or primary.done or primary.error
                ):
                    self._launch(race, 1, frame)
                if all(attempt.done for attempt in race.attempts):
                    for buffered, direction in primary.buffered:
                        self._emit(buffered, direction)
                    assert primary.error is not None
                    self._emit(primary.error, FrameDirection.UPSTREAM)
                    self._emit(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
                    return
                try:
                    async with asyncio.timeout(
                        max(remaining, 0) if len(race.attempts) == 1 else None
                    ):
                        await race.changed.wait()
                except TimeoutError:
                    pass
            # Cancellation/HTTP cleanup must not hold back winning output.
            loser_cleanup = self.create_task(
                self._cancel_attempts(race, except_winner=True)
            )
            while not race.winner.done:
                race.changed.clear()
                await race.changed.wait()
        except asyncio.CancelledError:
            race.interrupted = True
            raise
        finally:
            # An interruption can arrive after the winner has finished, while
            # the loser is still closing. Finish cleanup before the next turn
            # can reuse either service, even if cancellation arrives here.
            cleanup = self.create_task(self._finish_race(race, loser_cleanup))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    race.interrupted = True
                    cancelled = True
            if len(race.attempts) == 2:
                logger.info(
                    "LLM fallback: winner={}, primary={}, fallback={}",
                    race.winner.index if race.winner else None,
                    race.attempts[0].first_output or race.attempts[0].error,
                    race.attempts[1].first_output or race.attempts[1].error,
                )
            if cancelled:
                raise asyncio.CancelledError

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # The wrapper owns the ordered data queue. Child direct-mode tasks are
        # cancelled in _generate's finally before an interruption is forwarded.
        await FrameProcessor.process_frame(self, frame, direction)
        if isinstance(frame, StartFrame) and self._output_task is None:
            self._output_task = self.create_task(self._drain_output())
        if isinstance(frame, LLMContextFrame):
            await self._generate(frame)
            return
        if isinstance(frame, LLMUpdateSettingsFrame) and frame.service in (None, self):
            delta = frame.delta or type(self.primary._settings).from_mapping(
                frame.settings
            )
            await self._update_settings(delta)
        else:
            indices = (
                [0] if isinstance(frame, LLMContextSummaryRequestFrame) else [0, 1]
            )
            for index in indices:
                # Control frames may release a winning response's deferred
                # transition or speculative output after generation finishes.
                token = _attempt.set(
                    None
                    if isinstance(frame, LLMContextSummaryRequestFrame)
                    else self._latest[index]
                )
                echo = _input_id.set(frame.id)
                try:
                    await self._services[index].queue_frame(frame, direction)
                finally:
                    _input_id.reset(echo)
                    _attempt.reset(token)
        await self._flush_output()
        # A flush probe traverses this single ordered queue on all three legs;
        # it never splits into branches or gets deduplicated by frame ID.
        await FrameProcessor.push_frame(self, frame, direction)
