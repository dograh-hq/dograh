"""Connect canonical diagnostics to the existing call observation paths."""

import asyncio

from loguru import logger

from . import delivery
from .base import EventBuffer
from .configuration import load_settings, registration
from .recorder import CallEventRecorder


class CallEventsSession:
    def __init__(self, *, settings, organization_id, run_id, workflow_id, engine):
        self.settings = settings
        self.organization_id = organization_id
        self.engine = engine
        self.buffer = EventBuffer()
        self.recorder = CallEventRecorder(
            sink=self.buffer,
            run_id=run_id,
            org_id=organization_id,
            workflow_id=workflow_id,
            engine=engine,
        )
        self._finished = False
        self._lock = asyncio.Lock()
        self._subscriptions = []
        self._task = None
        self._monitor = None

    def attach(self, task, user_aggregator, monitor):
        self._task = task

        def subscribe(source, event, method):
            def handler(_source, *args):
                method(*args)

            source.add_event_handler(event, handler)
            self._subscriptions.append((source, event, handler))

        for event, method in (
            ("on_user_turn_started", self.recorder.on_user_turn_started),
            ("on_user_turn_stopped", self.recorder.on_user_turn_stopped),
            ("on_user_turn_stop_timeout", self.recorder.on_user_turn_stop_timeout),
            ("on_user_mute_started", self.recorder.on_mute_started),
            ("on_user_mute_stopped", self.recorder.on_mute_stopped),
        ):
            subscribe(user_aggregator, event, method)
        for event, method in (
            ("on_idle_timeout", self.recorder.on_pipeline_idle_timeout),
            ("on_heartbeat_timeout", self.recorder.on_heartbeat_timeout),
            ("on_pipeline_error", self.recorder.on_pipeline_error),
        ):
            subscribe(task, event, method)
        latency = task.user_bot_latency_observer
        if latency:
            for name in (
                "on_latency_measured",
                "on_latency_breakdown",
                "on_first_bot_speech_latency",
            ):
                subscribe(latency, name, getattr(self.recorder, name))
        monitor.on_idle_diagnostic = self.recorder.on_user_turn_idle
        self._monitor = monitor

    async def finish(self, gathered_context=None):
        try:
            await self._finish(gathered_context)
        except Exception as exc:
            logger.warning(
                "Call event finalization failed for org {} ({})",
                self.organization_id,
                type(exc).__name__,
            )
            await self.recorder.cleanup()

    async def _finish(self, gathered_context=None):
        async with self._lock:
            if self._finished:
                return
            workers = [self._task]
            for agent in [
                getattr(self.engine, "active_agent", None),
                *getattr(self.engine, "_retired_agents", []),
            ]:
                worker = getattr(agent, "worker", None)
                if worker is not None and worker not in workers:
                    workers.append(worker)
            try:
                async with asyncio.timeout(2):
                    await asyncio.gather(
                        *(w.wait_for_observers() for w in workers if w is not None)
                    )
            except TimeoutError:
                logger.warning(
                    "Call event observer drain timed out for org {}",
                    self.organization_id,
                )
            # Observer queues are drained by the call owner first. Pipecat runs
            # each event callback in its own task; wait for those already issued
            # for every event this session subscribed to, on every source.
            pending = {
                t
                for source, name, _ in self._subscriptions
                for event, t in getattr(source, "_event_tasks", ())
                if event == name and t is not asyncio.current_task() and not t.done()
            }
            if pending:
                await asyncio.wait(pending, timeout=2)
            self._finished = True
            context = (
                gathered_context
                if gathered_context is not None
                else getattr(self.engine, "_gathered_context", {})
            )
            self.recorder.call_ended(
                context.get("call_disposition")
                or context.get("mapped_call_disposition"),
                end_reason=context.get("call_status"),
            )
            await self.recorder.cleanup()
            for source, name, handler in self._subscriptions:
                source.remove_event_handler(name, handler)
            if self._monitor is not None:
                self._monitor.on_idle_diagnostic = None
            if self.buffer.dropped:
                logger.warning(
                    "Call event buffer dropped {} events for org {}",
                    self.buffer.dropped,
                    self.organization_id,
                )
            delivery.submit(
                self.organization_id,
                self.settings,
                self.buffer.seal(),
                self.buffer.size_bytes,
            )


async def create_session(*, organization_id, run_id, workflow_id, engine):
    try:
        async with asyncio.timeout(2):
            settings = await load_settings(organization_id)
        if not settings.enabled or not settings.sink_type:
            return None
        # Validate locally; no sink client or network I/O during capture.
        registration(settings.sink_type).config_model.model_validate(settings.config)
        return CallEventsSession(
            settings=settings,
            organization_id=organization_id,
            run_id=run_id,
            workflow_id=workflow_id,
            engine=engine,
        )
    except Exception as exc:
        logger.warning(
            "Call event collection unavailable for org {} ({})",
            organization_id,
            type(exc).__name__,
        )
        return None
