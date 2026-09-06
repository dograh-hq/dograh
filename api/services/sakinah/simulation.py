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
from pipecat.utils.enums import RealtimeFeedbackType

from api.db import db_client
from api.db.models import UserModel
from api.enums import CallType, WorkflowRunMode
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.ws_sender_registry import (
    register_ws_sender,
    unregister_ws_sender,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.sakinah.calm.runtime import EXPERIMENT_MODES, CalmSimulationRuntime
from api.services.sakinah.calm_evaluation import CalmEvaluator, run_llm_inference
from api.services.sakinah.internal_transport import (
    InternalTransport,
    create_internal_transport_pair,
)
from api.services.sakinah.session_store import create_pending_session, finish_session
from api.services.sakinah.workflow import (
    ensure_sakinah_workflow,
    ensure_service_user_workflow,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs

SAKINAH_ROLE = "sakinah"
SERVICE_USER_ROLE = "service_user"

BOT_TEXT_EVENT = RealtimeFeedbackType.BOT_TEXT.value

DEFAULT_MAX_DURATION_SECONDS = 300
MAX_ALLOWED_DURATION_SECONDS = 900

# Give Sakinah a head start so she greets first and the service user replies.
SERVICE_USER_START_DELAY_SECONDS = 1.5

# How long to wait for pipelines to wind down gracefully before cancelling.
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 15.0


def _format_simulation_transcript(turns: list[dict[str, Any]]) -> str:
    role_labels = {"sakinah": "assistant", "service_user": "service user"}
    return "".join(
        f"[{turn.get('timestamp', '')}] {role_labels.get(turn.get('role'), turn.get('role', 'unknown'))}: {turn.get('text', '')}\n"
        for turn in turns
        if turn.get("text")
    )


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
        experiment_mode: str = "full_calm_prompt",
        user_id: int | None = None,
    ):
        self.id = simulation_id
        self.user_id = user_id
        self.organization_id = organization_id
        self.scenario = scenario
        self.max_duration_seconds = max_duration_seconds
        self.experiment_mode = experiment_mode
        self.calm_runtime = CalmSimulationRuntime(
            mode=experiment_mode, scenario=scenario
        )
        self._last_calm_utterance: str | None = None
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
        self.completed_turns: list[dict] = []
        self.active_turns: dict[str, dict] = {}
        self.evaluation_tasks: set[asyncio.Task] = set()
        self.evaluator: CalmEvaluator | None = None
        self._evaluation_llm: Any = None
        self._evaluation_llm_lock = asyncio.Lock()
        self._stopping = False
        self._finalized = False

    def aggregated_turns(self) -> list[dict]:
        """Merge streamed bot-text chunks into whole conversation turns.

        The feedback observer emits word/phrase-level ``rtf-bot-text`` chunks;
        consecutive chunks from the same role form one spoken turn.
        """
        if self.completed_turns:
            return [dict(turn) for turn in self.completed_turns]

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
            "experiment_mode": self.experiment_mode,
            "calm_scores": self.calm_runtime.turns[-1]["calm_scores"]
            if self.calm_runtime.turns
            else {},
            "calm_trend": self.calm_runtime.turns[-1]["trend"]
            if self.calm_runtime.turns
            else {},
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

    def get(
        self, simulation_id: str, organization_id: int, user_id: int | None = None
    ) -> Simulation:
        simulation = self._simulations.get(simulation_id)
        if (
            not simulation
            or simulation.organization_id != organization_id
            or (user_id is not None and simulation.user_id != user_id)
        ):
            raise KeyError(simulation_id)
        return simulation

    async def start_simulation(
        self,
        user: UserModel,
        scenario: str,
        max_duration_seconds: Optional[int] = None,
        experiment_mode: str = "full_calm_prompt",
        scenario_id: str | None = None,
        scenario_name: str | None = None,
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
        if experiment_mode not in EXPERIMENT_MODES:
            raise ValueError(f"Unsupported experiment mode: {experiment_mode}")
        max_duration = min(
            max_duration_seconds or DEFAULT_MAX_DURATION_SECONDS,
            MAX_ALLOWED_DURATION_SECONDS,
        )
        simulation = Simulation(
            simulation_id=simulation_id,
            organization_id=user.selected_organization_id,
            scenario=scenario,
            max_duration_seconds=max_duration,
            experiment_mode=experiment_mode,
            user_id=user.id,
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
                "experiment_mode": experiment_mode,
                "direction": CallType.INBOUND.value,
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
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

        await db_client.create_sakinah_run(
            session_id=simulation.id,
            user_id=user.id,
            agent_id=simulation.agents[SAKINAH_ROLE].workflow_id,
            run_id=simulation.agents[SAKINAH_ROLE].workflow_run_id,
            service_user_agent_id=simulation.agents[SERVICE_USER_ROLE].workflow_id,
            service_user_run_id=simulation.agents[SERVICE_USER_ROLE].workflow_run_id,
            scenario=scenario,
            started_at=simulation.started_at,
            experiment_mode=simulation.experiment_mode,
        )

        simulation.evaluator = CalmEvaluator(
            self._make_evaluation_inference(simulation)
        )

        create_pending_session(
            session_id=simulation_id,
            scenario=scenario,
            name="AI-to-AI simulation",
            workflow_id=simulation.agents[SAKINAH_ROLE].workflow_id,
            workflow_run_id=simulation.agents[SAKINAH_ROLE].workflow_run_id,
            organization_id=user.selected_organization_id,
            started_at=simulation.started_at,
            experiment_mode=simulation.experiment_mode,
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

    def _make_calm_prompt_callback(self, simulation: Simulation):
        async def prepare_prompt(engine, context) -> None:
            messages = getattr(context, "messages", [])
            latest_utterance = None
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        latest_utterance = content
                        break
            if (
                not latest_utterance
                or latest_utterance == simulation._last_calm_utterance
            ):
                return
            simulation._last_calm_utterance = latest_utterance
            conversation_context = [
                {
                    "role": message.get("role", "unknown"),
                    "text": message.get("content", ""),
                }
                for message in messages[-6:]
                if isinstance(message, dict)
            ]
            turn = simulation.calm_runtime.analyze_turn(
                latest_utterance,
                conversation_context=conversation_context,
            )
            # Analysis is applied only to Sakinah's LLM. The service-user
            # workflow has its own context and never receives this callback.
            await engine._update_llm_context(turn["prompt_sent_to_llm"], [])
            simulation.publish(
                {
                    "role": SAKINAH_ROLE,
                    "type": "calm-analysis",
                    "payload": turn,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

        return prepare_prompt

    def _make_calm_response_callback(self, simulation: Simulation):
        async def record_response(response: str) -> None:
            simulation.calm_runtime.record_response(response or "")

        return record_response

    def _make_event_sender(self, simulation: Simulation, role: str):
        async def sender(message: dict) -> None:
            event = {"role": role, **message}
            payload = dict(event.get("payload") or {})
            event["payload"] = payload

            if event.get("type") == BOT_TEXT_EVENT:
                text = str(payload.get("text") or "").strip()
                if text:
                    turn = simulation.active_turns.get(role)
                    if turn is None:
                        turn = {
                            "turn_id": str(uuid.uuid4()),
                            "role": role,
                            "text": "",
                            "final": False,
                            "timestamp": event.get("timestamp")
                            or payload.get("timestamp")
                            or datetime.now(UTC).isoformat(),
                        }
                        simulation.active_turns[role] = turn
                    turn["text"] = f"{turn['text']} {text}".strip()
                    payload["turn_id"] = turn["turn_id"]

            if event.get("type") == "rtf-bot-stopped-speaking":
                turn = simulation.active_turns.pop(role, None)
                if turn is not None:
                    turn["final"] = True
                    simulation.completed_turns.append(turn)
                    payload["turn_id"] = turn["turn_id"]

            simulation.publish(event)

            if event.get("type") == "rtf-bot-stopped-speaking" and payload.get(
                "turn_id"
            ):
                self._schedule_evaluation(
                    simulation,
                    role,
                    str(payload["turn_id"]),
                )

        return sender

    def _make_evaluation_inference(self, simulation: Simulation):
        async def inference(messages: list[dict], system_prompt: str) -> str | None:
            llm = await self._get_evaluation_llm(simulation)
            return await run_llm_inference(llm, messages, system_prompt)

        return inference

    async def _get_evaluation_llm(self, simulation: Simulation):
        if simulation._evaluation_llm is not None:
            return simulation._evaluation_llm
        async with simulation._evaluation_llm_lock:
            if simulation._evaluation_llm is not None:
                return simulation._evaluation_llm

            from api.services.configuration.ai_model_configuration import (
                get_effective_ai_model_configuration_for_workflow,
            )
            from api.services.managed_model_services import get_mps_correlation_id
            from api.services.pipecat.service_factory import create_llm_service

            agent = simulation.agents[SAKINAH_ROLE]
            workflow_run = await db_client.get_workflow_run(
                agent.workflow_run_id,
                organization_id=simulation.organization_id,
            )
            if workflow_run is None:
                raise RuntimeError("Evaluation workflow run is unavailable")
            workflow_configurations = (
                workflow_run.definition.workflow_configurations
                if workflow_run.definition
                else workflow_run.workflow.workflow_configurations
            ) or {}
            configuration = await get_effective_ai_model_configuration_for_workflow(
                organization_id=simulation.organization_id,
                workflow_configurations=workflow_configurations,
            )
            if configuration.llm is None:
                raise RuntimeError("No text LLM is configured for evaluation")
            simulation._evaluation_llm = create_llm_service(
                configuration,
                correlation_id=get_mps_correlation_id(workflow_run.initial_context),
                usage_context="calm_evaluation",
            )
            return simulation._evaluation_llm

    def _schedule_evaluation(
        self,
        simulation: Simulation,
        role: str,
        turn_id: str,
    ) -> None:
        simulation.publish(
            {
                "role": role,
                "type": "calm-evaluation",
                "payload": {
                    "turn_id": turn_id,
                    "role": role,
                    "status": "pending",
                    "result": None,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        task = asyncio.create_task(
            self._evaluate_turn(
                simulation,
                role,
                turn_id,
                list(simulation.completed_turns),
            ),
            name=f"calm-evaluation-{turn_id}",
        )
        simulation.evaluation_tasks.add(task)
        task.add_done_callback(simulation.evaluation_tasks.discard)

    async def _evaluate_turn(
        self,
        simulation: Simulation,
        role: str,
        turn_id: str,
        turns: list[dict],
    ) -> None:
        try:
            if simulation.evaluator is None:
                raise RuntimeError("CALM evaluator is unavailable")
            result = await simulation.evaluator.evaluate(
                role=role,
                turn_id=turn_id,
                turns=turns,
            )
            payload = {
                "turn_id": turn_id,
                "role": role,
                "status": "completed",
                "result": result.model_dump(mode="json"),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - evaluator failures are isolated
            logger.warning(
                f"Simulation {simulation.id}: CALM evaluation failed for "
                f"turn {turn_id}: {exc}"
            )
            payload = {
                "turn_id": turn_id,
                "role": role,
                "status": "failed",
                "result": None,
                "error": "Evaluation unavailable",
            }
        simulation.publish(
            {
                "role": role,
                "type": "calm-evaluation",
                "payload": payload,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

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
                calm_prompt_callback=(
                    self._make_calm_prompt_callback(simulation)
                    if agent.role == SAKINAH_ROLE
                    else None
                ),
                calm_response_callback=(
                    self._make_calm_response_callback(simulation)
                    if agent.role == SAKINAH_ROLE
                    else None
                ),
            )
            logger.info(f"Simulation {simulation.id}: {agent.role} pipeline finished")
        except asyncio.CancelledError:
            logger.info(f"Simulation {simulation.id}: {agent.role} pipeline cancelled")
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
            asyncio.create_task(self._handle_agent_finished(simulation, agent.role))

    async def _handle_agent_finished(self, simulation: Simulation, role: str) -> None:
        if simulation._stopping or simulation._finalized:
            return
        reason = "peer_finished" if simulation.error is None else "peer_failed"
        logger.info(
            f"Simulation {simulation.id}: {role} finished, stopping peer ({reason})"
        )
        await self.stop_simulation(
            simulation.id,
            simulation.organization_id,
            reason=reason,
            user_id=simulation.user_id,
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
            simulation.id,
            simulation.organization_id,
            reason="max_duration",
            user_id=simulation.user_id,
        )

    async def stop_simulation(
        self,
        simulation_id: str,
        organization_id: int,
        reason: str = "stopped",
        user_id: int | None = None,
    ) -> Simulation:
        simulation = self.get(simulation_id, organization_id, user_id)
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
            _done, still_pending = await asyncio.wait(
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
        final_status = "failed" if simulation.error else "completed"

        if simulation.evaluation_tasks:
            await asyncio.gather(
                *list(simulation.evaluation_tasks), return_exceptions=True
            )

        for agent in simulation.agents.values():
            unregister_ws_sender(agent.workflow_run_id)

        # Pipeline completion uploads artifacts before its task exits, but an
        # explicit reconciliation makes the durable Sakinah row correct even
        # if the upload handler and task teardown complete in adjacent event
        # loop turns.
        if simulation.user_id is not None:
            for agent in simulation.agents.values():
                try:
                    await db_client.sync_sakinah_run_artifacts(agent.workflow_run_id)
                except Exception as e:
                    logger.warning(
                        f"Simulation {simulation.id}: failed to reconcile "
                        f"{agent.role} artifacts: {e}"
                    )

        turns = simulation.aggregated_turns()
        try:
            finish_session(
                session_id=simulation.id,
                organization_id=simulation.organization_id,
                ended_at=simulation.ended_at,
                turns=turns,
                calm_turns=simulation.calm_runtime.turns,
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

        if simulation.user_id is not None:
            artifact_references: dict[str, Any] = {}
            for role, agent in simulation.agents.items():
                artifacts = await db_client.get_workflow_run_artifacts_for_user(
                    simulation.user_id, agent.workflow_run_id
                )
                if artifacts is not None:
                    artifact_references[role] = artifacts
            primary_artifacts = artifact_references.get(SAKINAH_ROLE, {})
            try:
                await db_client.complete_sakinah_run(
                    user_id=simulation.user_id,
                    session_id=simulation.id,
                    status=final_status,
                    ended_at=simulation.ended_at,
                    transcript=_format_simulation_transcript(turns),
                    conversation=turns,
                    preview_data={
                        "turns": turns,
                        "agents": simulation.snapshot()["agents"],
                        "calm_scores": simulation.snapshot()["calm_scores"],
                        "calm_trend": simulation.snapshot()["calm_trend"],
                    },
                    recording_url=primary_artifacts.get("recording_url"),
                    transcript_url=primary_artifacts.get("transcript_url"),
                    recording_file_reference=artifact_references,
                    calm_turns=simulation.calm_runtime.turns,
                    timings={
                        "duration_ms": (
                            simulation.ended_at - simulation.started_at
                        ).total_seconds()
                        * 1000
                    },
                )
            except Exception as e:
                logger.error(f"Simulation {simulation.id}: failed to save DB run: {e}")

        # Signal end-of-stream to audio listeners.
        for queue in list(simulation.audio_subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Publish a terminal state only after persistence and artifact
        # reconciliation finish. Consumers use this transition as the signal
        # that finalization is complete.
        simulation.status = final_status
        simulation.publish_status()
        logger.info(
            f"Simulation {simulation.id} finalized: status={simulation.status} "
            f"reason={simulation.stop_reason} turns={len(turns)}"
        )


simulation_manager = SimulationManager()
