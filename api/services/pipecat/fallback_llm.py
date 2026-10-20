"""Dograh's bounded race between unchanged Pipecat text LLM services.

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

from api.schemas.llm_fallback import FallbackCondition
from pipecat.frames.frames import (
    AssistantImageRawFrame,
    EagerEndOfTurnCancelFrame,
    ErrorFrame,
    Frame,
    FunctionCallsFromLLMInfoFrame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMContextSummaryRequestFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
    NodeTransitionStartedFrame,
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


@dataclass(frozen=True)
class FallbackRoute:
    condition: FallbackCondition
    service: LLMService


@dataclass
class _Attempt:
    index: int
    race: "_Race"
    task: asyncio.Task | None = None
    deadline: asyncio.Timeout | None = None
    error: ErrorFrame | None = None
    cancelled: bool = False
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
    """Race services at their output boundary, before tools or speech escape.

    Children must use ``enable_direct_mode=True``. Metadata, reasoning and TTFB
    metrics are not answer output. Text, images or prepared tool calls commit a
    winner synchronously. The composite owns speculation so buffered child text
    can still win without exposing it before the user's turn is confirmed.
    """

    def __init__(
        self,
        primary: LLMService,
        *,
        routes: Sequence[FallbackRoute],
        first_output_timeout_secs: float = 20,
    ):
        services = [primary]
        self._routes: dict[str, int] = {}
        self._fallback_after_secs: float | None = None
        for route in routes:
            if route.condition.type in self._routes:
                raise ValueError("Only one fallback per condition is supported")
            if route.service not in services:
                services.append(route.service)
            if route.service is primary:
                raise ValueError("Fallback must be a separate service")
            self._routes[route.condition.type] = services.index(route.service)
            if route.condition.type == "no_output":
                self._fallback_after_secs = route.condition.after_ms / 1000
        if not all(service._enable_direct_mode for service in services):
            raise ValueError("Fallback children must use enable_direct_mode=True")
        super().__init__(settings=primary._settings)
        self.primary = primary
        self._services = services
        self._first_output_timeout_secs = first_output_timeout_secs
        self._fallback_metrics = {"started": 0, "won": 0}
        self._latest: list[_Attempt | None] = [None] * len(services)
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
        # Only accepted output is observed externally. Collect internal timing
        # independently of public metrics preferences; it never selects a winner.
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
        # Extraction/QA callers use services before pipeline setup, so these
        # tasks have a local lifetime and are always joined here. Non-streamed
        # APIs expose output only when the complete answer is available.
        started = time.monotonic()
        tasks: dict[int, asyncio.Task] = {}
        errors: dict[int, Exception] = {}

        async def invoke(index):
            async with asyncio.timeout(self._first_output_timeout_secs):
                result = await self._services[index].run_inference(context, **kwargs)
                if not result or not result.strip():
                    raise ValueError("LLM completed without answer output")
                return result

        def launch(index):
            tasks[index] = asyncio.create_task(invoke(index))
            if index:
                self._fallback_metrics["started"] += 1

        launch(0)
        try:
            while True:
                # Inspect every settled result before considering another
                # target; a successful response can finish in the same tick.
                for index, task in tasks.items():
                    if task.done() and index not in errors:
                        try:
                            result = task.result()
                        except Exception as exc:  # noqa: BLE001 - any pre-output provider error is eligible
                            errors[index] = exc
                        else:
                            if index:
                                self._fallback_metrics["won"] += 1
                            return result
                error_index = self._routes.get("error")
                if errors and error_index is not None and error_index not in tasks:
                    launch(error_index)
                slow_index = self._routes.get("no_output")
                remaining = None
                if (
                    slow_index is not None
                    and slow_index not in tasks
                    and not tasks[0].done()
                ):
                    assert self._fallback_after_secs is not None
                    remaining = self._fallback_after_secs - (time.monotonic() - started)
                    if remaining <= 0:
                        launch(slow_index)
                        remaining = None
                pending = [task for task in tasks.values() if not task.done()]
                if not pending:
                    raise errors[0]
                await asyncio.wait(
                    pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
        finally:

            async def close():
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)

            cleanup = asyncio.create_task(close())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError

    async def _update_settings(self, delta: LLMSettings) -> dict[str, Any]:
        changed = await self.primary._update_settings(delta)
        # A workflow's prompt reaches all targets. Model, sampling and provider
        # extras remain independently configured, including reasoning defaults.
        for service in self._services[1:]:
            await service._update_settings(
                LLMSettings(system_instruction=delta.system_instruction)
            )
        return changed

    async def _prepare_tools(
        self, service: LLMService, items: Sequence[FunctionCallRunnerItem]
    ) -> None:
        attempt = _attempt.get()
        if attempt and items:
            self._choose(attempt)
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
            await LLMService.push_frame(self, frame, direction)

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
        tool_boundary = isinstance(
            frame,
            (
                FunctionCallsFromLLMInfoFrame,
                FunctionCallsStartedFrame,
                NodeTransitionStartedFrame,
            ),
        ) and bool(frame.function_calls)
        if tool_boundary:
            if race.winner is not None and race.winner is not attempt:
                raise asyncio.CancelledError
            if self._speculation_gate.is_speculating:
                # Speculative tools must wait for a committed transcript, just
                # as on a standalone Pipecat service.
                race.interrupted = True
                self._emit(EagerEndOfTurnCancelFrame(), FrameDirection.UPSTREAM)
                self._emit(EagerEndOfTurnCancelFrame(), FrameDirection.DOWNSTREAM)
                race.changed.set()
                raise asyncio.CancelledError
            self._choose(attempt)
            if race.winner is not attempt:
                raise asyncio.CancelledError
        elif (
            isinstance(frame, LLMTextFrame) and bool(frame.text.strip())
        ) or isinstance(frame, AssistantImageRawFrame):
            self._choose(attempt)
        if isinstance(frame, MetricsFrame):
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
        elif race.winner is None and not isinstance(frame, LLMFullResponseEndFrame):
            attempt.buffered.append((frame, direction))

    def _choose(self, attempt: _Attempt) -> None:
        race = attempt.race
        if (
            race.winner is not None
            or attempt.error is not None
            or race.interrupted
            or race.superseded
            or asyncio.current_task() is not attempt.task
        ):
            return
        attempt.first_output = time.monotonic() - race.started
        race.winner = attempt
        if attempt.deadline:
            attempt.deadline.reschedule(None)
        if attempt.index:
            self._fallback_metrics["won"] += 1
        for frame, direction in attempt.buffered:
            self._emit(frame, direction, attempt)
        attempt.buffered.clear()
        race.changed.set()

    async def _run_attempt(self, attempt: _Attempt, frame: LLMContextFrame) -> None:
        token = _attempt.set(attempt)
        try:
            async with asyncio.timeout(self._first_output_timeout_secs) as deadline:
                attempt.deadline = deadline
                await self._services[attempt.index].queue_frame(frame)
        except asyncio.CancelledError:
            # Preserve real failures recorded before cancellation during cleanup.
            attempt.cancelled = attempt.error is None
            raise
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
                    error="LLM completed without answer output", processor=self
                )
            attempt.race.changed.set()
            _attempt.reset(token)

    def _launch(self, race: _Race, index: int, frame: LLMContextFrame) -> None:
        attempt = _Attempt(index=index, race=race)
        race.attempts.append(attempt)
        self._latest[index] = attempt
        if index:
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
        self._speculation_gate.begin_speculation(frame.speculation)
        # The outer gate buffers only the winner's speculative output. Let the
        # child boundary see it so arbitration doesn't depend on metrics.
        frame = replace(frame, speculation=False)
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
                if race.interrupted:
                    return
                primary = race.attempts[0]
                launched = {attempt.index for attempt in race.attempts}
                failed = any(attempt.error for attempt in race.attempts)
                error_index = self._routes.get("error")
                if failed and error_index is not None and error_index not in launched:
                    self._launch(race, error_index, frame)
                    launched.add(error_index)
                slow_index = self._routes.get("no_output")
                remaining = None
                if (
                    slow_index is not None
                    and slow_index not in launched
                    and not primary.done
                    and primary.error is None
                    and self._fallback_after_secs is not None
                ):
                    remaining = self._fallback_after_secs - (
                        time.monotonic() - race.started
                    )
                    if remaining <= 0:
                        self._launch(race, slow_index, frame)
                        remaining = None
                if all(attempt.done or attempt.error for attempt in race.attempts):
                    self._emit(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
                    assert primary.error is not None
                    self._emit(primary.error, FrameDirection.UPSTREAM)
                    self._emit(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
                    return
                try:
                    async with asyncio.timeout(remaining):
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
            if len(race.attempts) > 1:
                logger.info(
                    "LLM fallback: winner={}, interrupted={}, attempts={}",
                    race.winner.index if race.winner else None,
                    race.interrupted,
                    [
                        {
                            "index": a.index,
                            "first_output": a.first_output,
                            "failed": a.error is not None and not a.cancelled,
                            "cancelled": a.cancelled,
                        }
                        for a in race.attempts
                    ],
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
                [0]
                if isinstance(frame, LLMContextSummaryRequestFrame)
                else range(len(self._services))
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
        await LLMService.push_frame(self, frame, direction)
