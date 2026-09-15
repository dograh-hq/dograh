"""The in-call agent handoff transaction.

One agent hands the live caller to another. The call, its transport, its
recording and its conversation are untouched; what changes is which
:class:`AgentRuntime` owns generation.

Phases, in order::

    IDLE -> ANNOUNCING -> HOLD/PREPARING -> COMMITTING -> OPENING -> IDLE

*Announcing* plays the source agent's transfer message and waits for the
caller to actually hear it. *Hold* deactivates the source agent -- which gates
inference structurally, since an inactive worker is handed no frames from the
bus -- and starts the ringer. Recognition keeps running throughout, so
anything the caller says while waiting is aggregated into the shared context
and handed to the destination at commit rather than lost. *Preparing* builds
the destination and compacts the conversation concurrently, under the ringer.
*Committing* installs the compacted history, the destination's prompt and
tools, and activates it. *Opening* stops the ringer and lets the destination
introduce itself, exactly once.

Until the commit succeeds, every failure path restores the source agent with
its conversation intact: nothing is mutated before that point. After it, going
back is a new transfer, not a rollback.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from loguru import logger
from pipecat.frames.frames import LLMMessagesAppendFrame

from api.services.pipecat.agent_runtime_factory import AgentBuildError
from api.services.pipecat.audio_playback import play_hold_audio_loop
from api.services.workflow.agent_handoff_context import (
    build_handoff_snapshot,
    messages_after_boundary,
)
from api.services.workflow.agent_runtime import AgentRuntime, new_visit_id

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


# Ceiling on everything between accepting a transfer and the destination
# speaking. The caller is listening to a ringer for all of it.
TRANSFER_PREPARE_TIMEOUT_SECONDS = 20.0

# How long the destination gets to acknowledge its activation. Activation is a
# bus round trip to a worker that has already started, so this is short.
TRANSFER_ACTIVATION_TIMEOUT_SECONDS = 5.0

# Minimum ring the caller hears even if the destination is ready immediately.
# An instant handoff sounds like a glitch rather than a connection.
TRANSFER_MIN_HOLD_SECONDS = 1.2


class _TransferAbandoned(Exception):
    """The call ended or the request was invalidated mid-handoff."""


class TransferPhase(str, Enum):
    """Where a handoff has got to."""

    IDLE = "idle"
    ANNOUNCING = "announcing"
    PREPARING = "preparing"
    COMMITTING = "committing"
    OPENING = "opening"


@dataclass
class TransferRequest:
    """One accepted request to hand the caller to another agent."""

    destination_workflow_id: int
    destination_label: str
    origin_visit_id: str
    announcement: str | None = None
    request_id: str = field(default_factory=lambda: f"xfer-{uuid.uuid4().hex[:10]}")
    cancelled_reason: str | None = None

    @property
    def cancelled(self) -> bool:
        """Whether this request has been invalidated since it was accepted."""
        return self.cancelled_reason is not None


class AgentTransferCoordinator:
    """Runs agent handoffs for one call, one at a time."""

    def __init__(self, engine: "PipecatEngine"):
        self._engine = engine
        self._phase = TransferPhase.IDLE
        self._request: TransferRequest | None = None
        self._task: asyncio.Task | None = None
        self._commit_lock = asyncio.Lock()
        self._hold_stop: asyncio.Event | None = None
        self._hold_task: asyncio.Task | None = None
        self._completed: list[dict[str, Any]] = []

    # -- state -----------------------------------------------------------

    @property
    def phase(self) -> TransferPhase:
        """The current handoff phase."""
        return self._phase

    @property
    def in_progress(self) -> bool:
        """Whether a handoff is running right now."""
        return self._phase is not TransferPhase.IDLE

    @property
    def completed(self) -> list[dict[str, Any]]:
        """Outcome records for handoffs attempted on this call."""
        return list(self._completed)

    # -- entry points ----------------------------------------------------

    def accept(self, request: TransferRequest) -> bool:
        """Take ownership of a handoff request, refusing a concurrent one.

        Returns False when another handoff is already running: two agents must
        never be mid-handover on one call.
        """
        if self.in_progress:
            running = self._request.request_id if self._request else "another handoff"
            logger.warning(
                f"[transfer] refusing {request.request_id}: "
                f"{running} is already in progress"
            )
            return False
        if self._engine.is_call_disposed():
            logger.warning(f"[transfer] refusing {request.request_id}: call is ending")
            return False
        self._request = request
        self._phase = TransferPhase.ANNOUNCING
        return True

    def start(self, request: TransferRequest) -> None:
        """Run the accepted handoff on a task the engine owns.

        Deliberately not run from the tool's ``on_context_updated`` callback:
        the assistant aggregator cancels those when the caller interrupts, and
        a handoff interrupted halfway would leave the call with no active
        agent.
        """
        self._task = asyncio.create_task(
            self._run(request), name=f"agent-transfer:{request.request_id}"
        )
        self._task.add_done_callback(lambda t: self._report_task_result(request, t))

    def _report_task_result(self, request: TransferRequest, task: asyncio.Task) -> None:
        """Surface a handoff that died rather than finishing.

        Nothing awaits the handoff task, so an exception escaping ``_run`` --
        including one raised while unwinding -- would otherwise be swallowed
        and the call left in whatever state it reached.
        """
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        logger.opt(exception=error).error(
            f"[transfer] {request.request_id} task failed outside its own "
            "error handling; the call may be left without an active agent"
        )
        self._phase = TransferPhase.IDLE

    async def invalidate(self, reason: str) -> None:
        """Stop any handoff in flight; the call is ending or has moved on.

        No later callback may activate an agent after this: the request is
        marked cancelled before the task is touched, so a phase that wakes up
        mid-cancellation still sees it.
        """
        request = self._request
        if request is not None and not request.cancelled:
            request.cancelled_reason = reason
            logger.info(f"[transfer] {request.request_id} invalidated: {reason}")

        await self._stop_hold_audio()

        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None
        self._phase = TransferPhase.IDLE

    # -- the transaction -------------------------------------------------

    async def _run(self, request: TransferRequest) -> None:
        engine = self._engine
        source = engine.active_agent
        destination: AgentRuntime | None = None
        started_at = time.monotonic()
        committed = False

        if source is None or source.visit_id != request.origin_visit_id:
            # The agent that asked is no longer the agent running. Whatever
            # replaced it owns the call now.
            await self._finish(request, "stale_origin", source=None)
            return

        try:
            # 3. Announce on the source agent's own voice, and wait until the
            #    caller has actually heard it before anything else moves.
            await self._announce(request, source)
            await self._check_still_wanted(request)

            # 4. Hold. Deactivating the source gates inference without muting
            #    the caller, so recognition and recording carry on.
            await self._begin_hold(request, source)

            # 5. Prepare the destination and compact the conversation at the
            #    same time, both bounded by one deadline.
            async with asyncio.timeout(TRANSFER_PREPARE_TIMEOUT_SECONDS):
                self._phase = TransferPhase.PREPARING
                destination, snapshot = await asyncio.gather(
                    self._build_destination(request),
                    self._compact(request, source),
                )
                ready = await destination.wait_until_started()
            if not ready:
                raise AgentBuildError(
                    "destination_not_ready",
                    f"Agent visit {destination.visit_id} never started",
                )
            if destination.error:
                raise AgentBuildError(
                    "destination_unusable",
                    f"Agent visit {destination.visit_id}: {destination.error}",
                )

            await self._check_still_wanted(request, destination)

            # A handoff that completes instantly sounds broken. One ring is
            # what tells the caller they were connected to someone else.
            elapsed = time.monotonic() - started_at
            if elapsed < TRANSFER_MIN_HOLD_SECONDS:
                await asyncio.sleep(TRANSFER_MIN_HOLD_SECONDS - elapsed)

            # 6. Commit. Past this point the destination owns the call and
            #    there is nothing to roll back to.
            await self._commit(request, source, destination, snapshot)
            committed = True
        except _TransferAbandoned:
            # Already unwound by `_check_still_wanted`.
            return
        except asyncio.CancelledError:
            logger.info(f"[transfer] {request.request_id} cancelled mid-flight")
            self._signal_hold_stop()
            raise
        except AgentBuildError as e:
            logger.warning(f"[transfer] {request.request_id} failed: {e.message}")
            await self._rollback(request, source, destination, e.reason)
            return
        except asyncio.TimeoutError:
            logger.warning(
                f"[transfer] {request.request_id} timed out preparing "
                f"'{request.destination_label}'"
            )
            await self._rollback(request, source, destination, "prepare_timeout")
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[transfer] {request.request_id} errored: {e}")
            await self._rollback(request, source, destination, "internal_error")
            return

        assert committed and destination is not None
        try:
            # 7. Open, with the ringer stopped first so the destination is not
            #    speaking over it.
            await self._stop_hold_audio()
            self._phase = TransferPhase.OPENING
            await self._open(destination)

            # 8. Retire the source. Local: it never touches the call. Its
            #    visit was closed at commit, so this only releases its worker.
            await source.retire("transferred")
        except Exception as e:  # noqa: BLE001
            # The handoff succeeded; the destination is live and talking to the
            # caller. Failing to open or to release the previous agent is worth
            # reporting, but it is not a reason to undo a committed transfer.
            logger.exception(
                f"[transfer] {request.request_id} committed but could not "
                f"finish cleanly: {e}"
            )

        await self._finish(request, "completed", source=source, destination=destination)

    # -- phases ----------------------------------------------------------

    async def _announce(self, request: TransferRequest, source: AgentRuntime) -> None:
        """Play the transfer message on the source agent's voice."""
        self._phase = TransferPhase.ANNOUNCING
        if not request.announcement:
            return

        engine = self._engine
        engine.arm_speech_playback()
        spoken = await engine.queue_text_message(
            request.announcement, append_to_context=True, mute_user=True
        )
        if not spoken or not await engine.wait_for_speech_playback():
            # The announcement was never spoken, so nothing will arrive to
            # lift the mute it was queued under. Carry on with the handoff --
            # but not with a caller who can no longer be heard.
            engine.clear_queued_speech_mute()

        # Whatever the source agent still has in flight reaches the caller
        # before its worker is taken out of the conversation. The probe
        # travels through the agent's own worker and back, so this drains both
        # pipelines.
        await engine.drain_call_pipeline()

    async def _begin_hold(self, request: TransferRequest, source: AgentRuntime) -> None:
        """Take the source agent out of the conversation and start the ringer."""
        engine = self._engine

        # Deactivate first: from here the source agent is handed no frames
        # from the bus, so nothing it might say can race what follows.
        await engine.deactivate_agent(source)

        # Background writers must not mutate the conversation while it is
        # being snapshotted.
        await engine.pause_background_context_writers()

        self._hold_stop = asyncio.Event()
        self._hold_task = asyncio.create_task(
            play_hold_audio_loop(
                stop_event=self._hold_stop,
                sample_rate=engine.hold_audio_sample_rate,
                queue_frame=engine.transport_output_queue_frame,
            ),
            name=f"agent-transfer-hold:{request.request_id}",
        )

    async def _build_destination(self, request: TransferRequest) -> AgentRuntime:
        return await self._engine.build_agent(
            workflow_id=request.destination_workflow_id,
            visit_id=new_visit_id(),
        )

    async def _compact(self, request: TransferRequest, source: AgentRuntime):
        engine = self._engine
        return await build_handoff_snapshot(
            engine.context,
            source.inference_llm,
            request_id=request.request_id,
            parent_context=engine._get_otel_context(),
        )

    async def _commit(
        self,
        request: TransferRequest,
        source: AgentRuntime,
        destination: AgentRuntime,
        snapshot,
    ) -> None:
        """Install the destination agent under a short lock."""
        engine = self._engine
        async with self._commit_lock:
            self._phase = TransferPhase.COMMITTING
            await self._check_still_wanted(request, destination)

            # Anything the caller said while waiting sits after the snapshot
            # boundary and has not been summarized; it goes across verbatim.
            tail = messages_after_boundary(engine.context, snapshot.boundary)
            engine.context.set_messages([*snapshot.messages, *tail])
            logger.info(
                f"[transfer] {request.request_id} handing over "
                f"{len(snapshot.messages)} summarized + {len(tail)} live messages"
            )

            destination.entered_at = time.time()
            engine.install_agent(destination, previous=source)

            # The destination's prompt and tools, composed from its own start
            # node, land on its own LLM and on the shared context.
            await engine.set_node(
                destination.workflow.start_node_id,
                origin_visit_id=destination.visit_id,
            )

            activated = await engine.activate_agent(
                destination, timeout=TRANSFER_ACTIVATION_TIMEOUT_SECONDS
            )
            if not activated:
                raise AgentBuildError(
                    "activation_failed",
                    f"Agent visit {destination.visit_id} did not acknowledge activation",
                )

    async def _open(self, destination: AgentRuntime) -> None:
        """Run exactly one opening turn for the destination agent.

        Its own start node, opened the way a fresh call to it would be: the
        configured greeting if it has one, otherwise a first generation. A
        caller handed to another agent expects that agent to introduce itself.
        """
        await self._engine.queue_node_opening(
            node_id=destination.workflow.start_node_id,
            previous_node_id=None,
            generate_if_no_greeting=True,
            origin_visit_id=destination.visit_id,
        )

    # -- failure ---------------------------------------------------------

    async def _rollback(
        self,
        request: TransferRequest,
        source: AgentRuntime | None,
        destination: AgentRuntime | None,
        reason: str,
    ) -> None:
        """Put the source agent back, with its conversation as it was.

        Only reachable before a successful commit, where nothing has been
        mutated: the context still holds the full uncompacted history plus
        whatever the caller said during the hold.
        """
        await self._stop_hold_audio()

        if destination is not None:
            destination.exit_reason = "rolled_back"
            destination.exited_at = time.time()
            await destination.retire("transfer rolled back")
            self._engine.discard_pending_agent(destination)

        if source is None or self._engine.is_call_disposed():
            await self._finish(request, reason, source=source)
            return

        # A failure inside the commit may already have made the destination
        # the active agent. Put the source back before waking it, or the call
        # would be generating from one agent and speaking through another.
        self._engine.restore_agent(source)
        await self._engine.activate_agent(
            source, timeout=TRANSFER_ACTIVATION_TIMEOUT_SECONDS
        )
        await self._finish(request, reason, source=source)

        # Tell the agent, in its own words, that the handoff did not happen.
        # It already returned its tool result, so this is the only way back
        # into the conversation.
        try:
            await source.queue_frame(
                LLMMessagesAppendFrame(
                    [
                        {
                            "role": "user",
                            "content": (
                                "System note: the transfer to "
                                f"{request.destination_label} could not be "
                                "completed and you are still speaking with the "
                                "caller. Apologize briefly for the wait, do not "
                                "mention technical details, and continue helping "
                                "them yourself."
                            ),
                        }
                    ],
                    run_llm=True,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[transfer] could not resume source agent: {e}")

    async def _check_still_wanted(
        self, request: TransferRequest, destination: AgentRuntime | None = None
    ) -> None:
        """Abort the handoff if the call has moved on, without activating anyone.

        Raises:
            _TransferAbandoned: The request was invalidated or the call is
                ending. The source agent is left where the hold put it; the
                call is on its way out and nothing should speak.
        """
        if not (request.cancelled or self._engine.is_call_disposed()):
            return
        logger.info(
            f"[transfer] {request.request_id} abandoned "
            f"({request.cancelled_reason or 'call ended'})"
        )
        await self._stop_hold_audio()
        if destination is not None:
            await destination.retire("transfer abandoned")
            self._engine.discard_pending_agent(destination)
        self._phase = TransferPhase.IDLE
        raise _TransferAbandoned(request.cancelled_reason or "call ended")

    def _signal_hold_stop(self) -> None:
        """Ask the ringer to stop without awaiting anything.

        Used from cancellation handlers, where a further await would be
        cancelled straight away.
        """
        if self._hold_stop is not None:
            self._hold_stop.set()

    async def _stop_hold_audio(self) -> None:
        """Stop the ringer, bounded, and wait for the producer to notice."""
        self._signal_hold_stop()
        task = self._hold_task
        self._hold_task = None
        self._hold_stop = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("[transfer] hold audio did not stop within 2s")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[transfer] hold audio ended badly: {e}")

    async def _finish(
        self,
        request: TransferRequest,
        outcome: str,
        *,
        source: AgentRuntime | None,
        destination: AgentRuntime | None = None,
    ) -> None:
        self._phase = TransferPhase.IDLE
        self._request = None
        record = {
            "request_id": request.request_id,
            "outcome": outcome,
            "destination_workflow_id": request.destination_workflow_id,
            "destination_label": request.destination_label,
            "from_visit_id": source.visit_id if source else None,
            "to_visit_id": destination.visit_id if destination else None,
        }
        self._completed.append(record)
        self._engine.record_transfer_outcome(record)
        logger.info(f"[transfer] {request.request_id} {outcome}")


async def workflow_uses_agent_transfer(
    workflow_graph: Any, organization_id: int
) -> bool:
    """Whether any node in ``workflow_graph`` can hand the call to another agent.

    Decides which pipeline shape the call gets. A workflow that never
    transfers runs the single-worker pipeline it always has; only one that
    can transfer pays for the split, and only that one can reach the split's
    failure modes.
    """
    from api.db import db_client
    from api.enums import ToolCategory

    tool_uuids: set[str] = set()
    for node in workflow_graph.nodes.values():
        for tool_uuid in getattr(node, "tool_uuids", None) or []:
            tool_uuids.add(tool_uuid)
    if not tool_uuids:
        return False

    try:
        tools = await db_client.get_tools_by_uuids(list(tool_uuids), organization_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not check for agent-transfer tools: {e}")
        return False
    return any(t.category == ToolCategory.TRANSFER_AGENT.value for t in tools)
