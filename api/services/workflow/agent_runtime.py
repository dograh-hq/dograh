"""Per-agent execution state beneath one call-owned :class:`PipecatEngine`.

A call has one engine, one conversation and one persistent pipeline. What a
*visit* to an agent owns -- the pinned workflow definition and graph, the node
it is on, the model clients resolved from that definition, and (when agent
transfer is enabled) the child worker running its generation stage -- lives
here, so replacing the agent is replacing one of these rather than rebuilding
the call.

Two shapes use the same object:

* **Child.** ``worker`` is a worker of its own, bridged onto the call's bus.
  Every cascade call runs this shape, whether or not it ever transfers.
  Retiring it tears down that worker without touching the call.
* **Call-owned.** ``worker`` is the call's own :class:`PipelineWorker` and
  ``is_child`` is False. This is the realtime shape: one speech-to-speech
  service consumes the caller's audio directly, so there is no generation
  stage to lift into a worker, and such a call cannot transfer.

Every asynchronous operation started on behalf of a visit carries its
``visit_id``, so a result arriving after the agent has changed can be
attributed to the visit that asked for it rather than to whichever agent
happens to be active when it lands.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.worker import PipelineWorker

if TYPE_CHECKING:
    from pipecat.processors.aggregators.llm_context import LLMContext

    from api.services.workflow.workflow_graph import Node, WorkflowGraph


# How long a retiring agent gets to drain and shut its worker down. Past this
# the call carries on without it: a stuck provider connection must not hold a
# live caller.
AGENT_RETIRE_TIMEOUT_SECONDS = 10.0


def new_visit_id() -> str:
    """Mint an identifier for one visit to one agent.

    A → B → A is three visits even though the first and third run the same
    definition, so this is per activation and never derived from the workflow.
    """
    return f"visit-{uuid.uuid4().hex[:12]}"


@dataclass
class AgentRuntime:
    """One agent's execution state and resources for one visit."""

    visit_id: str
    workflow_id: int
    definition_id: int | None
    workflow_name: str
    workflow: "WorkflowGraph"
    llm: Any
    inference_llm: Any
    variable_extraction_llm: Any
    # None only while the call worker is still being built; run setup binds it
    # through `PipecatEngine.call_worker`.
    worker: PipelineWorker | None = None
    tts: Any = None
    recording_router: Any = None
    user_config: Any = None
    runtime_configuration: dict[str, Any] = field(default_factory=dict)
    is_realtime: bool = False
    # False for the call-owned runtime, whose worker is the call itself and
    # must never be shut down by a retirement.
    is_child: bool = False
    current_node: "Node | None" = None
    entered_at: float | None = None
    exited_at: float | None = None
    # Why this visit ended: "transferred", "call_ended", "rolled_back", or
    # None while it is still running.
    exit_reason: str | None = None
    # Set when this visit's pipeline reported a failure it cannot work
    # through. A destination that fails before commit fails the transfer; the
    # running agent failing ends the call.
    error: str | None = None
    retired: bool = False
    greeting_override: dict | None = None
    tools: Any = None
    system_prompt: str = ""
    mcp_sessions: dict[str, Any] = field(default_factory=dict)
    tool_tasks: set[asyncio.Task] = field(default_factory=set, repr=False)

    def bind_tool(self, engine, handler):
        """Bind a tool's lifetime to this visit, including work across awaits."""

        @wraps(handler)
        async def bound(params):
            if not engine.agent_can_act(self):
                return
            task = asyncio.current_task()
            self.tool_tasks.add(task)
            try:
                await handler(params)
            finally:
                self.tool_tasks.discard(task)

        return bound

    async def cancel_tools(self):
        tasks = [t for t in self.tool_tasks if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_mcp_sessions(self):
        for session in reversed(list(self.mcp_sessions.values())):
            await session.close_managed()
        self.mcp_sessions.clear()

    _close_task: asyncio.Task | None = field(default=None, init=False, repr=False)

    # -- execution -------------------------------------------------------

    async def queue_frame(self, frame: Frame) -> None:
        """Queue a frame at the head of this agent's own pipeline."""
        if self.worker is None:
            logger.warning(
                f"Agent visit {self.visit_id} has no worker yet; dropping {frame}"
            )
            return
        await self.worker.queue_frame(frame)

    async def speak(
        self,
        text: str,
        *,
        append_to_context: bool = False,
        persist_to_logs: bool = True,
    ) -> None:
        """Speak configured text through this agent's TTS voice."""
        await self.queue_frame(
            TTSSpeakFrame(
                text,
                append_to_context=append_to_context,
                persist_to_logs=persist_to_logs,
            )
        )

    async def run_llm(self, context: "LLMContext") -> None:
        """Ask this agent's LLM for a generation against the shared context.

        Queued on the LLM service rather than on the worker so it lands after
        whatever gate sits in front of the generation stage, matching how the
        single-worker pipeline has always started a node's opening turn.
        """
        await self.llm.queue_frame(LLMContextFrame(context))

    # -- lifecycle -------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether this visit's worker is still able to do work."""
        if self.retired or self.worker is None:
            return False
        return not self.worker.has_finished()

    async def wait_until_started(self, timeout: float = 10.0) -> bool:
        """Wait for this agent's worker to reach a usable, started state.

        Registration, ``add_workers()`` returning, and an activation message
        being sent are all short of this: activation only takes effect once
        the worker has started (``BaseWorker._maybe_activate``), so a
        destination that is merely registered would swallow its activation.
        """
        if not self.is_child or self.worker is None:
            return True

        async def _wait() -> None:
            while self.worker.started_at is None:
                if self.worker.has_finished():
                    raise RuntimeError(
                        f"Agent worker '{self.worker.name}' finished before starting"
                    )
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(_wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(f"Agent visit {self.visit_id} never became ready: {e}")
            return False

    async def retire(self, reason: str | None = None) -> None:
        """Drain and release this visit without ending the call."""
        await self._close(reason, drain=True)

    async def abort(self, reason: str | None = None) -> None:
        """Cancel this visit locally; finish cleanup even if its caller exits."""
        await self._close(reason, drain=False)

    async def _close(self, reason: str | None, *, drain: bool) -> None:
        if self._close_task is None:
            self.retired = True
            self.exit_reason = self.exit_reason or reason
            self._close_task = asyncio.create_task(
                self._release(reason, drain=drain), name=f"retire:{self.visit_id}"
            )
        elif not drain and self.is_child and self.worker is not None:
            # A hangup can interrupt an earlier graceful retirement.
            await self.worker.cancel(reason=reason)
        await asyncio.shield(self._close_task)

    async def _release(self, reason: str | None, *, drain: bool) -> None:
        try:
            if (
                self.is_child
                and self.worker is not None
                and not self.worker.has_finished()
            ):
                if drain:
                    await self.worker.stop_when_done()
                else:
                    await self.worker.cancel(reason=reason)
                try:
                    await asyncio.wait_for(
                        self.worker.wait(), AGENT_RETIRE_TIMEOUT_SECONDS
                    )
                except TimeoutError:
                    logger.warning(f"Agent {self.visit_id} did not finish; cancelling")
                    await self.worker.cancel(reason=reason)
        finally:
            await self.close_mcp_sessions()

    def describe(self) -> dict[str, Any]:
        """Summarize this visit for the call's visit history."""
        return {
            "visit_id": self.visit_id,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "definition_id": self.definition_id,
            "runtime_configuration": dict(self.runtime_configuration),
            "entered_at": self.entered_at,
            "exited_at": self.exited_at,
            "exit_reason": self.exit_reason,
            "error": self.error,
            "node": self.current_node.name if self.current_node else None,
        }
