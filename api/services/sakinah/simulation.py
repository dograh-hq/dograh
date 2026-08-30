"""AI-to-AI simulation orchestration: Service User AI <-> Sakinah AI.

Adapted from the removed LoopTalk orchestrator (commit 45b00cd). Two real
Dograh workflow pipelines are wired together through an in-memory transport
pair; transcript events stream to browsers over a per-simulation event feed.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.enums import CallType, WorkflowRunMode
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.ws_sender_registry import (
    register_ws_sender,
    unregister_ws_sender,
)
from api.services.sakinah.internal_transport import (
    InternalTransport,
    create_internal_transport_pair,
)
from api.services.sakinah.session_store import create_pending_session, finish_session
from api.services.sakinah.workflow import (
    ensure_sakinah_workflow,
    ensure_service_user_workflow,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from pipecat.utils.enums import RealtimeFeedbackType

SAKINAH_ROLE = "sakinah"
SERVICE_USER_ROLE = "service_user"

BOT_TEXT_EVENT = RealtimeFeedbackType.BOT_TEXT.value

DEFAULT_MAX_DURATION_SECONDS = 300
MAX_ALLOWED_DURATION_SECONDS = 900

# Give Sakinah a head start so she greets first and the service user replies.
SERVICE_USER_START_DELAY_SECONDS = 1.5

# How long to wait for pipelines to wind down gracefully before cancelling.
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 15.0


class SimulationAuthorizationError(Exception):
    """A workflow run could not be authorized (quota/credits/config)."""


class SimulationAgent:
    """One side of the simulated conversation."""

    def __init__(
        self,
        role: str,
        workflow_id: int,
        workflow_run_id: int,
        transport: InternalTransport,
    ):
        self.role = role
        self.workflow_id = workflow_id
        self.workflow_run_id = workflow_run_id
        self.transport = transport
        self.pipeline_task: Optional[asyncio.Task] = None


class Simulation:
    """State for one running AI-to-AI simulation."""

    def __init__(
        self,
        simulation_id: str,
        organization_id: int,
        scenario: str,
        max_duration_seconds: int,
    ):
        self.id = simulation_id
        self.organization_id = organization_id
        self.scenario = scenario
        self.max_duration_seconds = max_duration_seconds
        self.status = "starting"
        self.stop_reason: Optional[str] = None
        self.error: Optional[str] = None
        self.started_at = datetime.now(UTC)
        self.ended_at: Optional[datetime] = None
        self.agents: Dict[str, SimulationAgent] = {}
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        # Listeners for the live conversation audio (raw 16 kHz mono s16le
        # PCM chunks). Only live audio is streamed; there is no backlog.
        self.audio_subscribers: set[asyncio.Queue] = set()
        self.watchdog_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._finalized = False

    def aggregated_turns(self) -> list[dict]:
        """Merge streamed bot-text chunks into whole conversation turns.

        The feedback observer emits word/phrase-level ``rtf-bot-text`` chunks;
        consecutive chunks from the same role form one spoken turn.
        """
        turns: list[dict] = []
        for event in self.events:
            if event.get("type") != BOT_TEXT_EVENT:
                continue
            text = ((event.get("payload") or {}).get("text") or "").strip()
            if not text:
                continue
            role = event.get("role")
            timestamp = event.get("timestamp") or (event.get("payload") or {}).get(
                "timestamp"
            )
            if turns and turns[-1]["role"] == role:
                turns[-1]["text"] = f"{turns[-1]['text']} {text}"
            else:
                turns.append(
                    {
                        "role": role,
                        "text": text,
                        "final": True,
                        "timestamp": timestamp,
                    }
                )
        return turns

    @property
    def turn_count(self) -> int:
        return len(self.aggregated_turns())

    def snapshot(self) -> dict[str, Any]:
        return {
            "simulation_id": self.id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "scenario": self.scenario,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "turn_count": self.turn_count,
            "agents": {
                role: {
                    "workflow_id": agent.workflow_id,
                    "workflow_run_id": agent.workflow_run_id,
                }
                for role, agent in self.agents.items()
            },
        }

    def publish(self, event: dict) -> None:
        self.events.append(event)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"Simulation {self.id}: subscriber queue full")

    def publish_status(self) -> None:
        self.publish(
            {
                "role": "system",
                "type": "simulation-status",
                "payload": self.snapshot(),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def subscribe(self) -> tuple[list[dict], asyncio.Queue]:
        """Return the event backlog and a queue for live events."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        backlog = list(self.events)
        self.subscribers.add(queue)
        return backlog, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def publish_audio(self, pcm: bytes) -> None:
        """Fan live PCM out to audio listeners; drop chunks on slow consumers."""
        for queue in list(self.audio_subscribers):
            try:
                queue.put_nowait(pcm)
            except asyncio.QueueFull:
                pass

    def subscribe_audio(self) -> asyncio.Queue:
        # ~250 chunks x 40 ms = 10 s of buffering before we drop.
        queue: asyncio.Queue = asyncio.Queue(maxsize=250)
        self.audio_subscribers.add(queue)
        return queue

    def unsubscribe_audio(self, queue: asyncio.Queue) -> None:
        self.audio_subscribers.discard(queue)


class SimulationManager:
    """Creates, tracks, and stops AI-to-AI simulations (in-process)."""

    def __init__(self):
        self._simulations: Dict[str, Simulation] = {}

    def get(self, simulation_id: str, organization_id: int) -> Simulation:
        simulation = self._simulations.get(simulation_id)
        if not simulation or simulation.organization_id != organization_id:
            raise KeyError(simulation_id)
        return simulation

    async def start_simulation(
        self,
        user: UserModel,
        scenario: str,
        max_duration_seconds: Optional[int] = None,
    ) -> Simulation:
        from api.constants import FASTAPI_WORKERS

        if FASTAPI_WORKERS > 1:
            # Simulation state is per-process: with multiple workers the
            # status/stop endpoints and the events/audio WebSockets will
            # randomly land on a worker that does not own this simulation.
            logger.warning(
                f"Starting simulation with FASTAPI_WORKERS={FASTAPI_WORKERS}: "
                "simulation endpoints will intermittently fail on other "
                "workers. Run a single worker until issue #4 (worker-sync) "
                "is resolved."
            )

        simulation_id = str(uuid.uuid4())
        max_duration = min(
            max_duration_seconds or DEFAULT_MAX_DURATION_SECONDS,
            MAX_ALLOWED_DURATION_SECONDS,
        )
        simulation = Simulation(
            simulation_id=simulation_id,
            organization_id=user.selected_organization_id,
            scenario=scenario,
            max_duration_seconds=max_duration,
        )

        sakinah_workflow = await ensure_sakinah_workflow(db_client, user)
        service_user_workflow = await ensure_service_user_workflow(db_client, user)

        sakinah_transport, service_user_transport = create_internal_transport_pair(
            name_a=f"sakinah-{simulation_id[:8]}",
            name_b=f"service-user-{simulation_id[:8]}",
        )
        # Each agent's spoken audio is forwarded to browser listeners. The
        # two outputs together form the whole conversation, and turns
        # alternate, so interleaving chunks by arrival order is sufficient.
        sakinah_transport.set_audio_sink(simulation.publish_audio)
        service_user_transport.set_audio_sink(simulation.publish_audio)

        roles = [
            (SAKINAH_ROLE, sakinah_workflow, sakinah_transport),
            (SERVICE_USER_ROLE, service_user_workflow, service_user_transport),
        ]
        for role, workflow, transport in roles:
            initial_context = {
                "scenario": scenario,
                "simulation_id": simulation_id,
                "simulation_role": role,
                "direction": CallType.INBOUND.value,
            }
            if role == SERVICE_USER_ROLE:
                # The service user must not speak first: it stays silent until
                # Sakinah's greeting arrives through the internal transport,
                # which prevents both agents from talking over each other at
                # conversation start.
                initial_context["suppress_initial_greeting"] = True
            run_inputs = await prepare_workflow_run_inputs(
                db_client, workflow, initial_context=initial_context
            )
            run = await db_client.create_workflow_run(
                f"Sakinah sim {role} {simulation_id[:8]}",
                workflow.id,
                WorkflowRunMode.SMALLWEBRTC.value,
                user.id,
                call_type=CallType.INBOUND,
                organization_id=user.selected_organization_id,
                definition_id=run_inputs.definition_id,
                initial_context=run_inputs.initial_context,
            )
            simulation.agents[role] = SimulationAgent(
                role=role,
                workflow_id=workflow.id,
                workflow_run_id=run.id,
                transport=transport,
            )

        # Authorize both runs before any billable runtime starts (mirrors the
        # WebRTC signaling route). For managed model services this also mints
        # the per-run correlation id the pipeline requires.
        for role, agent in simulation.agents.items():
            quota_result = await authorize_workflow_run_start(
                workflow_id=agent.workflow_id,
                organization_id=user.selected_organization_id,
                workflow_run_id=agent.workflow_run_id,
                actor_user=user,
            )
            if not quota_result.has_quota:
                raise SimulationAuthorizationError(
                    quota_result.error_message
                    or f"Workflow run authorization failed for {role}"
                )

        create_pending_session(
            session_id=simulation_id,
            scenario=scenario,
            name="AI-to-AI simulation",
            workflow_id=simulation.agents[SAKINAH_ROLE].workflow_id,
            workflow_run_id=simulation.agents[SAKINAH_ROLE].workflow_run_id,
            organization_id=user.selected_organization_id,
            started_at=simulation.started_at,
        )

        # Register transcript-event senders BEFORE the pipelines start:
        # _run_pipeline picks the sender up from the registry during setup.
        for role, agent in simulation.agents.items():
            register_ws_sender(
                agent.workflow_run_id, self._make_event_sender(simulation, role)
            )

        for role, agent in simulation.agents.items():
            delay = (
                SERVICE_USER_START_DELAY_SECONDS if role == SERVICE_USER_ROLE else 0.0
            )
            agent.pipeline_task = asyncio.create_task(
                self._run_agent(simulation, agent, user, delay)
            )

        simulation.watchdog_task = asyncio.create_task(self._watchdog(simulation))
        simulation.status = "running"
        self._simulations[simulation_id] = simulation
        simulation.publish_status()
        logger.info(
            f"Started Sakinah simulation {simulation_id} "
            f"(sakinah run {simulation.agents[SAKINAH_ROLE].workflow_run_id}, "
            f"service user run "
            f"{simulation.agents[SERVICE_USER_ROLE].workflow_run_id})"
        )
        return simulation

    def _make_event_sender(self, simulation: Simulation, role: str):
        async def sender(message: dict) -> None:
            simulation.publish(
                {
                    "role": role,
                    **message,
                }
            )

        return sender

    async def _run_agent(
        self,
        simulation: Simulation,
        agent: SimulationAgent,
        user: UserModel,
        start_delay: float,
    ) -> None:
        # Import here to avoid a heavy import chain at module import time.
        from api.services.pipecat.run_pipeline import _run_pipeline

        try:
            if start_delay:
                await asyncio.sleep(start_delay)
            # The peer may have already failed during our start delay; the
            # stop signal fired before this pipeline could register its
            # disconnect handler, so starting now would stall shutdown until
            # the hard-cancel timeout.
            if simulation._stopping or simulation._finalized:
                logger.info(
                    f"Simulation {simulation.id}: skipping {agent.role} start, "
                    "simulation is stopping"
                )
                return
            audio_config = AudioConfig(
                transport_in_sample_rate=16000,
                transport_out_sample_rate=16000,
                vad_sample_rate=16000,
                pipeline_sample_rate=16000,
            )
            await _run_pipeline(
                agent.transport,
                agent.workflow_id,
                agent.workflow_run_id,
                user.id,
                call_context_vars={},
                audio_config=audio_config,
                user_provider_id=str(user.provider_id),
                organization_id=simulation.organization_id,
            )
            logger.info(
                f"Simulation {simulation.id}: {agent.role} pipeline finished"
            )
        except asyncio.CancelledError:
            logger.info(
                f"Simulation {simulation.id}: {agent.role} pipeline cancelled"
            )
            raise
        except Exception as e:
            logger.error(
                f"Simulation {simulation.id}: {agent.role} pipeline failed: {e}",
                exc_info=True,
            )
            simulation.error = f"{agent.role} pipeline failed: {e}"
            simulation.publish(
                {
                    "role": agent.role,
                    "type": "pipeline-error",
                    "payload": {"error": str(e), "fatal": True},
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        finally:
            # Whichever pipeline ends first (normally or on failure) takes the
            # other one down with it.
            asyncio.create_task(
                self._handle_agent_finished(simulation, agent.role)
            )

    async def _handle_agent_finished(self, simulation: Simulation, role: str) -> None:
        if simulation._stopping or simulation._finalized:
            return
        reason = "peer_finished" if simulation.error is None else "peer_failed"
        logger.info(
            f"Simulation {simulation.id}: {role} finished, stopping peer "
            f"({reason})"
        )
        await self.stop_simulation(
            simulation.id, simulation.organization_id, reason=reason
        )

    async def _watchdog(self, simulation: Simulation) -> None:
        try:
            await asyncio.sleep(simulation.max_duration_seconds)
        except asyncio.CancelledError:
            return
        logger.info(
            f"Simulation {simulation.id}: max duration "
            f"{simulation.max_duration_seconds}s reached, stopping"
        )
        await self.stop_simulation(
            simulation.id, simulation.organization_id, reason="max_duration"
        )

    async def stop_simulation(
        self, simulation_id: str, organization_id: int, reason: str = "stopped"
    ) -> Simulation:
        simulation = self.get(simulation_id, organization_id)
        if simulation._stopping or simulation._finalized:
            return simulation
        simulation._stopping = True
        simulation.stop_reason = reason
        simulation.status = "stopping"
        simulation.publish_status()

        # Never cancel the watchdog from inside the watchdog itself: stopping
        # a simulation on max-duration runs stop_simulation *in* that task, and
        # self-cancellation would abort the shutdown before finalization.
        if (
            simulation.watchdog_task
            and not simulation.watchdog_task.done()
            and simulation.watchdog_task is not asyncio.current_task()
        ):
            simulation.watchdog_task.cancel()

        # Ask both pipelines to hang up gracefully. The registered
        # on_client_disconnected handler ends the call through the engine,
        # which lets the normal completion path (transcripts, artifacts,
        # workflow run state) run for each pipeline.
        for agent in simulation.agents.values():
            task = agent.pipeline_task
            if task is None or task.done():
                continue
            try:
                await agent.transport._call_event_handler(
                    "on_client_disconnected", agent.transport
                )
            except Exception as e:
                logger.warning(
                    f"Simulation {simulation.id}: failed to signal disconnect "
                    f"for {agent.role}: {e}"
                )

        pending = [
            agent.pipeline_task
            for agent in simulation.agents.values()
            if agent.pipeline_task is not None
        ]
        if pending:
            done, still_pending = await asyncio.wait(
                pending, timeout=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
            )
            for task in still_pending:
                logger.warning(
                    f"Simulation {simulation.id}: pipeline did not stop "
                    "gracefully, cancelling"
                )
                task.cancel()
            if still_pending:
                await asyncio.gather(*still_pending, return_exceptions=True)

        await self._finalize(simulation)
        return simulation

    async def _finalize(self, simulation: Simulation) -> None:
        if simulation._finalized:
            return
        simulation._finalized = True
        simulation.ended_at = datetime.now(UTC)
        simulation.status = "failed" if simulation.error else "completed"

        for agent in simulation.agents.values():
            unregister_ws_sender(agent.workflow_run_id)

        turns = simulation.aggregated_turns()
        try:
            finish_session(
                session_id=simulation.id,
                organization_id=simulation.organization_id,
                ended_at=simulation.ended_at,
                turns=turns,
                timings={
                    "duration_ms": (
                        simulation.ended_at - simulation.started_at
                    ).total_seconds()
                    * 1000
                },
            )
        except Exception as e:
            logger.error(
                f"Simulation {simulation.id}: failed to save session JSON: {e}"
            )

        # Signal end-of-stream to audio listeners.
        for queue in list(simulation.audio_subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        simulation.publish_status()
        logger.info(
            f"Simulation {simulation.id} finalized: status={simulation.status} "
            f"reason={simulation.stop_reason} turns={len(turns)}"
        )


simulation_manager = SimulationManager()
