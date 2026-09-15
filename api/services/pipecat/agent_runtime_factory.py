"""Building an :class:`AgentRuntime` for one visit to one workflow.

Call setup creates the first runtime and attaches its child worker here.
Transfers build later runtimes under the hold ringer, using the destination's
pinned definition and model configuration. Both use the same generation stage.

Everything call-scoped -- transport, recording, recognition, the shared
context, the call timer -- stays on the call pipeline and is never rebuilt.
What this creates is one agent's own LLM and TTS clients and the child worker
that runs them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from api.db import db_client
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.agent_generation_processor import (
    AgentGenerationProcessor,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.pipeline_builder import (
    build_agent_generation_pipeline,
    create_agent_worker,
)
from api.services.pipecat.recording_router_processor import RecordingRouterProcessor
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_tts_service,
)
from api.services.workflow.agent_runtime import AgentRuntime, new_visit_id
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.run_creation import definition_to_run
from api.services.workflow.workflow_graph import WorkflowGraph
from pipecat.pipeline.worker import PipelineWorker


class AgentBuildError(Exception):
    """A destination agent could not be built.

    Carries a machine-readable reason so a failed transfer can report why
    without leaking configuration detail to the caller.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class AgentGenerationCallbacks:
    """Per-visit callbacks wired into an agent's generation stage."""

    generation_started: Callable[[], Any]
    llm_text_frame: Callable[[str], Any]


class AgentRuntimeFactory:
    """Creates agent runtimes against one call's resources."""

    def __init__(
        self,
        *,
        organization_id: int,
        workflow_run_id: int,
        call_worker: PipelineWorker,
        audio_config: AudioConfig | None,
        callbacks_factory: Callable[[str], AgentGenerationCallbacks],
        fetch_recording_audio: Callable[..., Any] | None = None,
        has_recordings: bool = False,
        mps_correlation_id: str | None = None,
        on_agent_error: Callable[[AgentRuntime, Any], Any] | None = None,
        use_draft: bool = False,
    ):
        self._on_agent_error = on_agent_error
        self._use_draft = use_draft
        self._organization_id = organization_id
        self._workflow_run_id = workflow_run_id
        self._call_worker = call_worker
        self._audio_config = audio_config
        self._callbacks_factory = callbacks_factory
        self._fetch_recording_audio = fetch_recording_audio
        self._has_recordings = has_recordings
        self._mps_correlation_id = mps_correlation_id

    @property
    def organization_id(self) -> int:
        """The organization every agent in this call must belong to."""
        return self._organization_id

    async def resolve_destination(self, workflow_id: int) -> tuple[Any, Any]:
        """Look up a destination workflow and pin the definition to run.

        Scoped to the call's organization: a workflow id that belongs to
        another tenant resolves to nothing, exactly as if it did not exist.
        Version selection follows the call it joins, so a call testing the
        first agent's draft transfers to the destination's draft too.
        """
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=self._organization_id
        )
        if not workflow:
            raise AgentBuildError(
                "destination_not_found",
                f"No workflow {workflow_id} in organization {self._organization_id}",
            )

        # Unscoped by itself, but the lookup above already established that
        # this workflow belongs to the call's organization.
        definition = await definition_to_run(
            db_client, workflow, use_draft=self._use_draft
        )
        if definition is None or not definition.workflow_json:
            raise AgentBuildError(
                "destination_not_published",
                f"Workflow {workflow_id} has no published definition to run",
            )
        return workflow, definition

    async def build(
        self,
        *,
        workflow_id: int,
        visit_id: str | None = None,
    ) -> AgentRuntime:
        """Build and attach a child worker for one visit to ``workflow_id``.

        The worker starts inactive and is added under the call worker, so its
        lifetime follows the call: it is torn down with the call even if the
        engine never activates it.

        Raises:
            AgentBuildError: The destination cannot host an in-call visit.
        """
        visit_id = visit_id or new_visit_id()
        workflow, definition = await self.resolve_destination(workflow_id)
        run_configs = definition.workflow_configurations or {}

        from api.services.configuration.ai_model_configuration import (
            get_effective_ai_model_configuration_for_workflow,
        )

        user_config = await get_effective_ai_model_configuration_for_workflow(
            organization_id=self._organization_id,
            workflow_configurations=run_configs,
        )

        if user_config.is_realtime and user_config.realtime is not None:
            # A realtime destination is a different pipeline shape, not a
            # different set of services: it has no STT or TTS stage for the
            # call pipeline to feed. Reject it rather than half-building it.
            raise AgentBuildError(
                "destination_is_realtime",
                f"Workflow {workflow_id} runs a speech-to-speech model, which "
                "cannot take over a cascade call",
            )

        workflow_graph = WorkflowGraph(
            ReactFlowDTO.model_validate(definition.workflow_json),
            skip_instance_constraints_for={"trigger"},
        )

        llm = create_llm_service(user_config, correlation_id=self._mps_correlation_id)
        tts = create_tts_service(
            user_config, self._audio_config, correlation_id=self._mps_correlation_id
        )
        # Same client policy as the run setup: the conversation LLM also
        # serves out-of-band inference, and extraction gets a separately
        # tagged client only where the run would have created one, so a
        # handoff does not quietly double this agent's provider connections.
        needs_extraction_llm = workflow_graph.uses_variable_extraction() or bool(
            (run_configs.get("call_dispositions") or [])
        )
        inference_llm = llm
        variable_extraction_llm = (
            create_llm_service(
                user_config,
                correlation_id=self._mps_correlation_id,
                usage_context="variable_extraction",
            )
            if needs_extraction_llm
            and user_config.llm.provider == ServiceProviders.DOGRAH.value
            else llm
        )

        recording_router = None
        if self._has_recordings and self._fetch_recording_audio is not None:
            recording_router = RecordingRouterProcessor(
                audio_sample_rate=(
                    self._audio_config.pipeline_sample_rate
                    if self._audio_config
                    else 16000
                ),
                fetch_recording_audio=self._fetch_recording_audio,
            )

        runtime = AgentRuntime(
            visit_id=visit_id,
            workflow_id=workflow_id,
            definition_id=definition.id,
            workflow_name=workflow.name,
            workflow=workflow_graph,
            llm=llm,
            inference_llm=inference_llm,
            variable_extraction_llm=variable_extraction_llm,
            tts=tts,
            recording_router=recording_router,
            user_config=user_config,
            runtime_configuration={
                "stt_provider": user_config.stt.provider,
                "stt_model": user_config.stt.model,
                "tts_provider": user_config.tts.provider,
                "tts_model": user_config.tts.model,
                "llm_provider": user_config.llm.provider,
                "llm_model": user_config.llm.model,
            },
            is_child=True,
            entered_at=None,
        )
        try:
            await self.attach(runtime)
        except BaseException:
            await runtime.abort("preparation cancelled")
            raise
        logger.info(
            f"[transfer] built agent visit {visit_id} for workflow {workflow_id} "
            f"({workflow.name}) definition={definition.id} "
            f"v{definition.version_number}/{definition.status} "
            f"llm={user_config.llm.provider}/{user_config.llm.model} "
            f"tts={user_config.tts.provider}/{user_config.tts.model}"
        )
        return runtime

    async def attach(self, runtime: AgentRuntime) -> None:
        """Give ``runtime`` a worker of its own and start it under the call.

        Separate from building the services so the call's first agent can be
        attached once the call pipeline is running: a child's spans join the
        call's conversation trace, and that trace only exists from the moment
        the call pipeline starts.
        """
        if runtime.worker is not None:
            return

        callbacks = self._callbacks_factory(runtime.visit_id)
        generation_callbacks = AgentGenerationProcessor(
            generation_started_callback=callbacks.generation_started,
            llm_text_frame_callback=callbacks.llm_text_frame,
        )
        pipeline = build_agent_generation_pipeline(
            runtime.llm,
            runtime.tts,
            generation_callbacks,
            recording_router=runtime.recording_router,
        )
        call_tracing_context = getattr(self._call_worker, "_tracing_context", None)
        if call_tracing_context is None:
            # Not fatal -- the call runs fine untraced -- but it is invisible
            # from the trace itself, which comes out with turns and no
            # generations inside them rather than with anything missing.
            logger.warning(
                f"[transfer] visit {runtime.visit_id} attached without the call's "
                "tracing context; this agent's LLM and TTS will emit no spans"
            )
        runtime.worker = create_agent_worker(
            pipeline,
            name=runtime.visit_id,
            audio_config=self._audio_config,
            call_tracing_context=call_tracing_context,
            call_worker_name=self._call_worker.name,
        )

        if self._on_agent_error is not None:
            # An agent's errors stay inside its own worker, so the call
            # pipeline's termination funnel never sees them. That is
            # deliberate: only the engine knows whether the failing agent is
            # the one talking to the caller.
            @runtime.worker.event_handler("on_pipeline_error")
            async def _on_agent_pipeline_error(_worker, frame):
                await self._on_agent_error(runtime, frame)

        # Children inherit the call worker's lifetime: the runner starts this
        # one right away and tears it down with the call, so an agent prepared
        # for a transfer that never commits cannot outlive the call.
        await self._call_worker.add_workers(runtime.worker)
