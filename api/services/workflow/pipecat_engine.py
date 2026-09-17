from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    Iterable,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallResultProperties,
    UserIdleTimeoutUpdateFrame,
)
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.settings import LLMSettings
from pipecat.utils.enums import EndTaskReason

from api.db import db_client
from api.enums import ToolCategory
from api.errors.failure import (
    classify_exception,
    failure_metadata_for_processor,
    log_failure,
)
from api.schemas.workflow_configurations import CallDispositionOption
from api.services.pipecat.audio_playback import play_audio
from api.services.workflow.agent_runtime import AgentRuntime, new_visit_id
from api.services.workflow.workflow_graph import Node, WorkflowGraph

if TYPE_CHECKING:
    from pipecat.frames.frames import Frame
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.utils.tracing.tracing_context import TracingContext

    LLMService = Union[OpenAILLMService, AnthropicLLMService, GoogleLLMService]

import asyncio
import time

from loguru import logger

from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY
from api.services.workflow import pipecat_engine_callbacks as engine_callbacks
from api.services.workflow.answer_handling import ANSWER_TERMINAL_REASONS, handle_answer
from api.services.workflow.disposition_extraction import (
    CALL_DISPOSITION_CONTEXT_KEY,
    DispositionExtractionService,
)
from api.services.workflow.disposition_mapping import (
    apply_disposition_mapping,
    get_disposition_mapping,
)
from api.services.workflow.initial_context import GREETING_OVERRIDE_CONTEXT_KEY
from api.services.workflow.mcp_tool_session import McpToolSession
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
    compose_system_prompt_for_node,
)
from api.services.workflow.pipecat_engine_context_summarizer import (
    ContextSummarizationManager,
)
from api.services.workflow.pipecat_engine_custom_tools import (
    CustomToolManager,
)
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.tools.knowledge_base import (
    retrieve_from_knowledge_base,
)
from api.utils.template_renderer import render_template

CALL_STATUS_CONTEXT_KEY = "call_status"

# Gathered-context keys the engine records itself. Variable extraction merges
# its results into the same dict, so these are held back from that merge.
#
# The call disposition is recorded only by the engine, which keeps its mapped
# counterpart in sync for reporting, filters and external-PBX write-backs.
ENGINE_OWNED_CONTEXT_KEYS = frozenset(
    {
        CALL_DISPOSITION_CONTEXT_KEY,
        "mapped_call_disposition",
        CALL_STATUS_CONTEXT_KEY,
        "call_tags",
        "answer_supervisor",
    }
)

# How long the terminal extraction gets before the call is disposed of without
# it. In-pipeline cancellations funnel through `end_call_with_reason`, so this
# coroutine is now the only thing that ends the pipeline: an LLM that never
# answers would otherwise hold the call, its telephony channel and every
# service behind it open indefinitely. Measured on the abrupt-hangup path at
# p50 1.4s / p90 4.0s / max 21.3s, so this cuts off the tail and nothing else.
FINAL_EXTRACTION_TIMEOUT_SECONDS = 10.0


class PipecatEngine:
    def __init__(
        self,
        *,
        task: Optional[PipelineWorker] = None,
        llm: Optional["LLMService"] = None,
        inference_llm: Optional["LLMService"] = None,
        variable_extraction_llm: Optional["LLMService"] = None,
        context: Optional[LLMContext] = None,
        workflow: WorkflowGraph,
        call_context_vars: dict,
        workflow_run_id: Optional[int] = None,
        node_transition_callback: Optional[
            Callable[[str, str, Optional[str], Optional[str], bool], Awaitable[None]]
        ] = None,
        embeddings_api_key: Optional[str] = None,
        embeddings_model: Optional[str] = None,
        embeddings_base_url: Optional[str] = None,
        embeddings_provider: Optional[str] = None,
        embeddings_endpoint: Optional[str] = None,
        embeddings_api_version: Optional[str] = None,
        has_recordings: bool = False,
        is_realtime: bool = False,
        context_compaction_enabled: bool = False,
        run_transition_variable_extraction_in_background: bool = True,
        call_dispositions: Sequence[CallDispositionOption] | None = None,
    ):
        self._call_worker = task
        self._is_realtime = is_realtime
        # LLM used for out-of-band inference (variable extraction, context
        # summarization). Falls back to the pipeline LLM when not provided.
        # In realtime mode the pipeline LLM is a speech-to-speech service
        # that does not implement run_inference, so a separate text LLM
        # must be passed in.
        resolved_inference_llm = inference_llm or llm
        # Execution state belongs to a visit; call resources stay on the engine.
        self._active_agent: AgentRuntime = AgentRuntime(
            visit_id=new_visit_id(),
            workflow_id=0,
            definition_id=None,
            workflow_name="",
            workflow=workflow,
            llm=llm,
            inference_llm=resolved_inference_llm,
            # Variable and disposition extraction can share a separately
            # tagged managed-model client without rerouting normal
            # conversation calls.
            variable_extraction_llm=variable_extraction_llm or resolved_inference_llm,
            worker=task,
            is_realtime=is_realtime,
            entered_at=time.time(),
        )
        self._active_agent.greeting_override = (call_context_vars or {}).get(
            GREETING_OVERRIDE_CONTEXT_KEY
        )
        self._agent_on_hold = False
        self._pending_agent: AgentRuntime | None = None
        self._retired_agents: list[AgentRuntime] = []
        self._agent_visits: list[dict] = []
        self._transfer_outcomes: list[dict] = []
        # Set by run setup on every cascade call; a realtime call gets none
        # and so can never transfer.
        self._agent_factory = None
        self._transfer_coordinator = None
        self.context = context
        self._call_context_vars = call_context_vars
        self._workflow_run_id = workflow_run_id
        self._node_transition_callback = node_transition_callback
        self._run_transition_variable_extraction_in_background = (
            run_transition_variable_extraction_in_background
        )
        self._call_dispositions = tuple(call_dispositions or ())
        self._initialized = False
        self._call_disposed = False
        self._shutdown_task: asyncio.Task | None = None
        self._gathered_context: dict = {}
        self._user_response_timeout_task: Optional[asyncio.Task] = None
        self._pending_extraction_tasks: set[asyncio.Task] = set()
        # True once terminal call disposal has run its synchronous extraction.
        # Recoverable operations such as a failed transfer use a repeatable
        # flush and must not consume this one-shot finalization state.
        self._final_extraction_done: bool = False

        # Will be set later in initialize() when we have
        # access to _context
        self._variable_extraction_manager = None
        self._disposition_extraction_service: Optional[DispositionExtractionService] = (
            None
        )

        # Track current LLM reference text for TTS aggregation correction
        self._current_llm_generation_reference_text: str = ""

        # Controls whether user input should be muted
        self._mute_pipeline: bool = False
        self.answer_supervisor = None
        self._answer_user_aggregator = None
        self._answer_idle_timeout = 0

        # Mute state for queued TTSSpeakFrames (transition speech, custom tool messages)
        # "idle" = not muting, "waiting" = speech queued, "playing" = bot speaking it
        self._queued_speech_mute_state: str = "idle"

        # Tracks whether the bot is currently speaking (for allow_interrupt logic)
        self._bot_is_speaking: bool = False

        # Playback tracking for speech a caller needs to await (see
        # arm_speech_playback / wait_for_speech_playback). Armed state is
        # "nothing started yet", so both events start cleared.
        self._speech_playback_started: asyncio.Event = asyncio.Event()
        self._speech_playback_finished: asyncio.Event = asyncio.Event()

        # Custom tool manager (initialized in initialize())
        self._custom_tool_manager: Optional[CustomToolManager] = None

        # Cached organization ID (resolved lazily from workflow run)
        self._organization_id: Optional[int] = None

        # The organization's disposition mapping, loaded once in initialize().
        # Held on the engine rather than fetched where it is used because the
        # two places that stamp a disposition -- set_call_disposition and
        # end_call_with_reason -- run on the teardown path, where a DB round
        # trip cannot be afforded (see end_call_with_reason). Empty means
        # "no mapping", which is the identity translation.
        self._disposition_mapping: dict[str, str] = {}

        # Embeddings configuration (passed from run_pipeline.py)
        self._embeddings_api_key: Optional[str] = embeddings_api_key
        self._embeddings_model: Optional[str] = embeddings_model
        self._embeddings_base_url: Optional[str] = embeddings_base_url
        self._embeddings_provider: Optional[str] = embeddings_provider
        self._embeddings_endpoint: Optional[str] = embeddings_endpoint
        self._embeddings_api_version: Optional[str] = embeddings_api_version

        # Audio configuration (set via set_audio_config from _run_pipeline)
        self._audio_config = None

        # Transport output processor for injecting audio directly into the
        # output, bypassing STT (set via set_transport_output from _run_pipeline)
        self._transport_output = None

        # Recording audio fetcher (set via set_fetch_recording_audio from _run_pipeline)
        self._fetch_recording_audio = None

        # True when the workflow has active recordings; enables recording
        # response mode instructions on all nodes for in-context learning.
        self._has_recordings: bool = has_recordings

        # Background context summarization on node transitions
        self._context_compaction_enabled: bool = context_compaction_enabled
        self._context_summarization_manager: Optional[ContextSummarizationManager] = (
            None
        )

    @property
    def active_agent(self) -> AgentRuntime:
        """The agent currently entitled to speak to the caller."""
        return self._active_agent

    @property
    def pending_agent(self) -> Optional[AgentRuntime]:
        """An agent prepared for a handoff that has not committed yet."""
        return self.__dict__.get("_pending_agent")

    @property
    def call_worker(self) -> Optional[PipelineWorker]:
        """The worker that lives for the whole call.

        Transport, recording, recognition, the shared aggregators and the call
        timer run here. An agent's generation stage may run in a worker of its
        own; this one outlives all of them. Not called "the task": with agent
        workers in play that name says nothing about which worker is meant,
        and ending the call, resolving the call trace and speaking a line each
        belong to a different one.
        """
        return self.__dict__.get("_call_worker")

    @call_worker.setter
    def call_worker(self, worker: Optional[PipelineWorker]) -> None:
        """Bind the call-scoped worker, which is built after the engine.

        A realtime call's only agent runs in this same worker, so binding the
        call worker also completes that agent. A cascade agent has a worker of
        its own and is left alone here.
        """
        self._call_worker = worker
        if not self._active_agent.is_child and self._active_agent.worker is None:
            self._active_agent.worker = worker

    async def _get_organization_id(self) -> Optional[int]:
        """Get and cache the organization ID from workflow run."""
        if self._organization_id is None:
            self._organization_id = (
                await db_client.get_organization_id_by_workflow_run_id(
                    self._workflow_run_id
                )
            )
        return self._organization_id

    def _get_otel_context(self):
        """Extract the OTel Context from the task's TracingContext.

        Returns the turn-level context if available, otherwise the
        conversation-level context, or None.
        """
        tracing_ctx: TracingContext | None = getattr(
            self.call_worker, "_tracing_context", None
        )
        if not tracing_ctx:
            return None
        return tracing_ctx.get_turn_context() or tracing_ctx.get_conversation_context()

    async def initialize(self):
        # TODO: May be set_node in a separate task so that we return from initialize immediately
        if self._initialized:
            logger.warning(f"{self.__class__.__name__} already initialized")
            return
        try:
            self._initialized = True

            # Helper that encapsulates variable extraction logic
            self._variable_extraction_manager = VariableExtractionManager(self)

            if self._call_dispositions:
                self._disposition_extraction_service = DispositionExtractionService(
                    llm=self.active_agent.variable_extraction_llm,
                    context=self.context,
                    options=self._call_dispositions,
                    template_context=self._call_context_vars,
                )

            # Helper that encapsulates custom tool management
            self._custom_tool_manager = CustomToolManager(self)

            # Loaded here, at call setup, so that stamping a disposition during
            # teardown stays synchronous. A failure to load leaves the identity
            # mapping in place: recording the untranslated disposition is worse
            # than the mapped one but far better than failing the call.
            try:
                self._disposition_mapping = await get_disposition_mapping(
                    await self._get_organization_id()
                )
            except Exception as e:
                logger.error(f"Error loading the organization disposition mapping: {e}")

            # Open persistent MCP server sessions for this call (degrades on failure)
            await self._open_mcp_sessions()

            # Helper that encapsulates context summarization
            if self._context_compaction_enabled:
                self._context_summarization_manager = ContextSummarizationManager(self)

            logger.debug(f"{self.__class__.__name__} initialized")
        except Exception as e:
            logger.error(f"Error initializing {self.__class__.__name__}: {e}")
            raise

    async def _update_llm_context(self, system_prompt: str, functions: list[dict]):
        """Update LLM settings with the composed system prompt and tool list."""

        # An empty node tool list must clear the previous node's tools before
        # providers update or reconnect their session.
        self.context.set_tools(ToolsSchema(standard_tools=functions))

        # For Gemini Live, set context on the LLM before _update_settings so that
        # _connect (triggered by reconnect) can read tools from it.
        if (
            hasattr(self.active_agent.llm, "_context")
            and not self.active_agent.llm._context
            and self.context
        ):
            self.active_agent.llm._context = self.context

        await self.active_agent.llm._update_settings(
            LLMSettings(system_instruction=system_prompt)
        )

    def _format_prompt(self, prompt: str) -> str:
        """Delegate prompt formatting to the shared workflow.utils implementation."""

        return render_template(prompt, self._call_context_vars)

    async def _create_transition_func(
        self,
        name: str,
        transition_to_node: str,
        transition_speech: Optional[str] = None,
        transition_speech_type: Optional[str] = None,
        transition_speech_recording_id: Optional[str] = None,
        *,
        agent: AgentRuntime | None = None,
    ):
        agent = agent or self.active_agent

        async def transition_func(function_call_params: FunctionCallParams) -> None:
            """Inner function that handles the node change tool calls"""
            logger.info(f"LLM Function Call EXECUTED: {name}")
            logger.info(
                f"Function: {name} -> transitioning to node: {transition_to_node}"
            )
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                # Perform variable extraction before transitioning to new node
                await self._perform_variable_extraction_if_needed(
                    self.active_agent.current_node,
                    run_in_background=self._run_transition_variable_extraction_in_background,
                )

                # Queue transition speech/audio before switching nodes
                speech_type = transition_speech_type or "text"
                if (
                    speech_type == "audio"
                    and transition_speech_recording_id
                    and self._fetch_recording_audio
                ):
                    logger.info(
                        f"Playing transition audio: {transition_speech_recording_id}"
                    )
                    self._queued_speech_mute_state = "waiting"
                    result = await self._fetch_recording_audio(
                        recording_pk=int(transition_speech_recording_id)
                    )
                    if result:
                        await play_audio(
                            result.audio,
                            sample_rate=(
                                self._audio_config.pipeline_sample_rate
                                if self._audio_config
                                else 16000
                            ),
                            queue_frame=self._transport_output.queue_frame,
                            transcript=result.transcript,
                            persist_to_logs=True,
                        )
                    else:
                        logger.warning(
                            f"Failed to fetch transition audio {transition_speech_recording_id}"
                        )
                elif transition_speech:
                    await self.queue_text_message(transition_speech, mute_user=True)

                # Set context for the new node, so that when the function call result
                # frame is received by LLMContextAggregator and an LLM generation
                # is done, we have updated context and functions
                await self.set_node(transition_to_node, origin_visit_id=agent.visit_id)

                is_end_node = agent.workflow.nodes[transition_to_node].is_end
                if is_end_node:
                    # The tool result triggers the end node's closing response.
                    # Arm before returning it: realtime can begin speaking before
                    # the aggregator's on_context_updated callback runs.
                    self.arm_speech_playback()
                    self._mute_pipeline = True

                async def on_context_updated() -> None:
                    """Finish an end node after its response reaches the caller."""
                    if is_end_node:
                        # This callback runs in its own task, leaving input audio,
                        # model generation, and transport playback free to continue.
                        # EndFrame closes realtime sessions; transport draining
                        # alone cannot recover audio the model hasn't sent yet.
                        await self.wait_for_speech_playback()
                        if self.agent_can_act(agent):
                            await self.end_call_with_reason(
                                EndTaskReason.END_CALL.value
                            )

                result = {"status": "done"}

                properties = FunctionCallResultProperties(
                    on_context_updated=on_context_updated,
                )

                # Call results callback from the pipecat framework
                # so that a new llm generation can be triggred if
                # required
                await function_call_params.result_callback(
                    result, properties=properties
                )

            except Exception as e:
                logger.error(f"Error in transition function {name}: {str(e)}")
                error_result = {"status": "error", "error": str(e)}
                await function_call_params.result_callback(error_result)

        return agent.bind_tool(self, transition_func)

    async def _register_transition_function_with_llm(
        self,
        name: str,
        transition_to_node: str,
        transition_speech: Optional[str] = None,
        transition_speech_type: Optional[str] = None,
        transition_speech_recording_id: Optional[str] = None,
        *,
        agent: AgentRuntime | None = None,
    ):
        agent = agent or self.active_agent
        logger.debug(
            f"Registering function {name} to transition to node {transition_to_node} with LLM"
        )

        # Create transition function
        transition_func = await self._create_transition_func(
            name,
            transition_to_node,
            transition_speech,
            transition_speech_type,
            transition_speech_recording_id,
            agent=agent,
        )

        # Register function with LLM
        agent.llm.register_function(
            name,
            transition_func,
            is_node_transition=True,
        )

    async def _register_knowledge_base_function(
        self, document_uuids: list[str], *, agent: AgentRuntime | None = None
    ) -> None:
        """Register knowledge base retrieval function with the LLM.

        Args:
            document_uuids: List of document UUIDs to filter the search by
        """
        logger.debug(
            f"Registering knowledge base retrieval function with {len(document_uuids)} document(s)"
        )

        agent = agent or self.active_agent

        async def retrieve_kb_func(function_call_params: FunctionCallParams) -> None:
            logger.info("LLM Function Call EXECUTED: retrieve_from_knowledge_base")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                query = function_call_params.arguments.get("query", "")
                organization_id = await self._get_organization_id()

                if not organization_id:
                    raise ValueError(
                        "Organization ID not available for knowledge base retrieval"
                    )

                result = await retrieve_from_knowledge_base(
                    query=query,
                    organization_id=organization_id,
                    document_uuids=document_uuids,
                    limit=3,  # Return top 3 most relevant chunks
                    embeddings_api_key=self._embeddings_api_key,
                    embeddings_model=self._embeddings_model,
                    embeddings_base_url=self._embeddings_base_url,
                    embeddings_provider=self._embeddings_provider,
                    embeddings_endpoint=self._embeddings_endpoint,
                    embeddings_api_version=self._embeddings_api_version,
                    correlation_id=self._call_context_vars.get(
                        MPS_CORRELATION_ID_CONTEXT_KEY
                    ),
                    tracing_context=self._get_otel_context(),
                )

                await function_call_params.result_callback(result)

            except Exception as e:
                logger.error(f"Knowledge base retrieval failed: {e}")
                await function_call_params.result_callback(
                    {"error": str(e), "chunks": [], "query": query, "total_results": 0}
                )

        # Register the function with the LLM
        agent.llm.register_function(
            "retrieve_from_knowledge_base", agent.bind_tool(self, retrieve_kb_func)
        )

    async def _perform_variable_extraction_if_needed(
        self,
        node: Optional[Node],
        run_in_background: bool = True,
    ) -> Optional[dict]:
        """Perform variable extraction if the node has extraction enabled.

        Args:
            node: The node to extract variables from.
            run_in_background: If True, runs extraction as a fire-and-forget task.
                If False, awaits the extraction synchronously.
        """
        if not (node and node.extraction_enabled and node.extraction_variables):
            return

        # Capture the current turn context for otel tracing
        # before creating the background task.
        parent_context = self._get_otel_context()

        extraction_prompt = self._format_prompt(node.extraction_prompt)
        node_variables = [
            variable
            for variable in node.extraction_variables
            if variable.name not in ENGINE_OWNED_CONTEXT_KEYS
        ]
        if not node_variables:
            logger.debug(
                f"No LLM-derived variables configured for node: {node.name}; "
                "skipping variable extraction"
            )
            return None
        extraction_variables = [
            (
                v.model_copy(update={"prompt": self._format_prompt(v.prompt)})
                if v.prompt
                else v
            )
            for v in node_variables
        ]

        async def _do_extraction() -> Optional[dict]:
            try:
                logger.debug(f"Starting variable extraction for node: {node.name}")
                extracted_data = (
                    await self._variable_extraction_manager._perform_extraction(
                        extraction_variables, parent_context, extraction_prompt
                    )
                )
                if not isinstance(extracted_data, dict):
                    logger.warning(
                        f"Variable extraction for node {node.name} returned "
                        f"{type(extracted_data).__name__} instead of dict, "
                        f"skipping update. Data: {extracted_data}"
                    )
                    return None
                requested_names = {variable.name for variable in extraction_variables}
                unexpected_names = extracted_data.keys() - requested_names
                if unexpected_names:
                    logger.warning(
                        f"Variable extraction for node {node.name} returned "
                        f"unrequested keys {sorted(unexpected_names)}; ignoring them"
                    )
                extracted_data = {
                    key: value
                    for key, value in extracted_data.items()
                    if key in requested_names
                }
                # Extraction variable names are author-supplied and nothing
                # validates them against the keys the engine owns. Let one
                # through and it would desynchronise the call's outcome:
                # `call_disposition` would carry the extracted value while
                # `mapped_call_disposition` -- which is what reporting, filters
                # and the PBX write-back all read -- kept the recorded one.
                self._gathered_context.update(
                    {
                        key: value
                        for key, value in extracted_data.items()
                        if key not in ENGINE_OWNED_CONTEXT_KEYS
                    }
                )
                extracted_variables = self._gathered_context.setdefault(
                    "extracted_variables", {}
                )
                extracted_variables.update(extracted_data)
                logger.debug(
                    f"Variable extraction completed for node: {node.name}. Extracted: {extracted_data}"
                )
                return extracted_data
            except Exception as e:
                metadata = failure_metadata_for_processor(
                    self.active_agent.variable_extraction_llm
                )
                log_failure(
                    classify_exception(
                        e,
                        source=metadata.source,
                        provider=metadata.provider,
                        error_owner=metadata.error_owner,
                    ),
                    organization_id=self._organization_id,
                    workflow_run_id=self._workflow_run_id,
                    node_name=node.name,
                )
                return None

        if run_in_background:
            logger.debug(
                f"Scheduling background variable extraction for node: {node.name}"
            )
            task = asyncio.create_task(
                _do_extraction(), name=f"variable-extraction:{node.name}"
            )
            self._pending_extraction_tasks.add(task)
            task.add_done_callback(self._pending_extraction_tasks.discard)
            return None
        else:
            logger.debug(
                f"Performing synchronous variable extraction for node: {node.name}"
            )
            return await _do_extraction()

    async def _await_pending_extractions(self, timeout: float = 30.0) -> None:
        """Await all in-flight background extraction tasks.

        Args:
            timeout: Maximum seconds to wait for pending extractions.
        """
        if not self._pending_extraction_tasks:
            return

        task_names = [t.get_name() for t in self._pending_extraction_tasks]
        logger.debug(
            f"Awaiting {len(self._pending_extraction_tasks)} pending extraction task(s): {task_names}"
        )
        start_time = asyncio.get_event_loop().time()
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*self._pending_extraction_tasks, return_exceptions=True),
                timeout=timeout,
            )
            elapsed = asyncio.get_event_loop().time() - start_time
            # Log any exceptions returned by gather
            for task_name, result in zip(task_names, results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Pending extraction task '{task_name}' failed: {result}"
                    )
            logger.debug(f"All pending extraction tasks completed in {elapsed:.2f}s")
        except asyncio.TimeoutError:
            incomplete = [
                t.get_name() for t in self._pending_extraction_tasks if not t.done()
            ]
            logger.warning(
                f"Timed out waiting for pending extraction tasks after {timeout}s. "
                f"Incomplete: {incomplete}"
            )

    async def flush_variable_extraction(self) -> Optional[dict]:
        """Refresh extracted variables without marking the call finalized.

        This operation is intentionally repeatable. Transfer routing and
        external-PBX field mappings need current conversation values, but a
        failed transfer can return control to the agent and gather more input.
        """
        await self._await_pending_extractions()
        return await self._perform_variable_extraction_if_needed(
            self.active_agent.current_node,
            run_in_background=False,
        )

    async def extract_variables_standalone(self, node: Node) -> Optional[dict]:
        """Run one node's extraction on an engine that never owned a pipeline.

        A text chat ends outside any pipeline -- every turn builds and tears one
        down, and the session-end request arrives with none running -- so its
        final extraction cannot come through ``_end_call``. This hands that path
        the single piece of ``initialize`` extraction needs, without opening MCP
        sessions or pushing LLM settings for a conversation that is already over.

        Callers own the resulting values; ``self._gathered_context`` is updated
        in place as usual, but nothing here persists it.
        """
        if self._variable_extraction_manager is None:
            self._variable_extraction_manager = VariableExtractionManager(self)
        self.active_agent.current_node = node
        return await self._perform_variable_extraction_if_needed(
            node, run_in_background=False
        )

    async def perform_final_variable_extraction(self) -> None:
        """Perform the one-shot variable extraction used during call disposal.

        Awaits any background extractions still running from previous nodes,
        then runs the current node's extraction inline. Idempotency prevents
        duplicate terminal extraction when multiple teardown paths converge.
        """
        if self._final_extraction_done:
            logger.debug("Final variable extraction already performed; skipping")
            return
        await self.flush_variable_extraction()
        self._final_extraction_done = True

    async def _prepare_node(
        self, agent: AgentRuntime, node: Node, *, apply_settings=True
    ) -> None:
        """Prepare one runtime's prompt and tools without changing the call."""
        manager = (
            self._custom_tool_manager
            if agent is self.active_agent
            else CustomToolManager(self, agent)
        )
        if not node.is_end:
            for edge in node.out_edges:
                await self._register_transition_function_with_llm(
                    edge.get_function_name(),
                    edge.target,
                    edge.transition_speech,
                    edge.data.transition_speech_type,
                    edge.data.transition_speech_recording_id,
                    agent=agent,
                )
        if node.tool_uuids and manager:
            await manager.register_handlers(
                node.tool_uuids,
                mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
            )
        if node.document_uuids:
            await self._register_knowledge_base_function(
                node.document_uuids, agent=agent
            )
        prompt = compose_system_prompt_for_node(
            node=node,
            workflow=agent.workflow,
            format_prompt=self._format_prompt,
            has_recordings=self._has_recordings,
        )
        functions = await compose_functions_for_node(
            node=node, custom_tool_manager=manager
        )
        agent.tools = ToolsSchema(standard_tools=functions)
        agent.system_prompt = prompt
        if apply_settings:
            await agent.llm._update_settings(LLMSettings(system_instruction=prompt))

    async def _setup_llm_context(self, node: Node) -> None:
        agent = self.active_agent
        await self._prepare_node(agent, node, apply_settings=False)
        if agent is self.active_agent:
            self.context.set_otel_span_name(f"llm-{node.name}")
            await self._update_llm_context(
                agent.system_prompt, agent.tools.standard_tools
            )

    async def set_node(
        self,
        node_id: str,
        emit_transition_event: bool = True,
        *,
        origin_visit_id: Optional[str] = None,
    ):
        """
        Simplified set_node implementation according to v2 PRD.

        Args:
            node_id: Node to enter, in the running agent's graph.
            emit_transition_event: Whether to report the transition to the UI.
            origin_visit_id: The agent visit requesting the change. A request
                from a visit that no longer owns the call is rejected: a tool
                call that landed just as a handoff committed must not move the
                new agent to a node id from the old agent's graph.
        """
        agent = self._active_agent
        if origin_visit_id is not None and origin_visit_id != agent.visit_id:
            logger.debug(
                f"Rejecting node change from retired visit {origin_visit_id}; "
                f"{agent.visit_id} owns the call"
            )
            return

        node = self.active_agent.workflow.nodes[node_id]

        logger.debug(
            f"Executing node: name: {node.name} allow_interrupt: {node.allow_interrupt} is_end: {node.is_end}"
        )

        # Track previous node for transition event
        previous_node_name = (
            self.active_agent.current_node.name
            if self.active_agent.current_node
            else None
        )
        previous_node_id = (
            self.active_agent.current_node.id
            if self.active_agent.current_node
            else None
        )

        # Set current node for all nodes (including static ones) so STT mute filter works
        self.active_agent.current_node = node

        # Track visited nodes in gathered context for call tags
        nodes_visited = self._gathered_context.setdefault("nodes_visited", [])
        if node.name not in nodes_visited:
            nodes_visited.append(node.name)

        # Send node transition event if callback is provided
        if emit_transition_event and self._node_transition_callback:
            try:
                await self._node_transition_callback(
                    node_id,
                    node.name,
                    previous_node_id,
                    previous_node_name,
                    node.allow_interrupt,
                )
            except Exception as e:
                # Log but don't fail - feedback is non-critical
                logger.debug(f"Failed to send node transition event: {e}")

        # Handle start nodes
        if node.is_start:
            await self._handle_start_node(node)
        # Handle end nodes
        elif node.is_end:
            await self._handle_end_node(node)
        # Handle normal agent nodes
        else:
            await self._handle_agent_node(node)

        # Summarize context in background after non-start node transitions
        # to clean up tool calls from previous nodes
        if previous_node_id is not None and self._context_summarization_manager:
            await self._context_summarization_manager.start()

    async def _handle_start_node(self, node: Node) -> None:
        """Set up context immediately; the answer supervisor owns initial listening."""
        await self._setup_llm_context(node)

    def set_answer_supervisor(self, supervisor, user_aggregator, idle_timeout: float):
        self.answer_supervisor = supervisor
        self._answer_user_aggregator = user_aggregator
        self._answer_idle_timeout = idle_timeout

    async def handle_answer_supervision(self):
        async def update_idle_timeout(timeout):
            await self._answer_user_aggregator.queue_frame(
                UserIdleTimeoutUpdateFrame(
                    timeout=self._answer_idle_timeout if timeout is None else timeout,
                )
            )

        await handle_answer(
            self, self.answer_supervisor, update_idle_timeout=update_idle_timeout
        )

    def get_node_greeting(self, node_id: str) -> Optional[tuple[str, Optional[str]]]:
        """Return the greeting info for a node, or None if not configured.

        Returns:
            A tuple of (greeting_type, value) where:
            - ("text", rendered_text) for text greetings spoken via TTS
            - ("audio", recording primary key) for configured audio greetings
            - ("audio_recording_id", recording ID) for call-level audio overrides
            Or None if no greeting is configured.
        """
        node = self.active_agent.workflow.nodes.get(node_id)
        if not node:
            return None

        # A programmatic override applies only to the workflow entry greeting;
        # greetings on later nodes continue to use their saved configuration.
        if node.is_start:
            override = self.active_agent.greeting_override
            if isinstance(override, dict):
                override_type = override.get("type")
                if override_type == "text":
                    text = override.get("text")
                    if isinstance(text, str) and text.strip():
                        return ("text", self._format_prompt(text))
                elif override_type == "audio":
                    recording_id = override.get("recording_id")
                    if isinstance(recording_id, str) and recording_id.strip():
                        return ("audio_recording_id", recording_id.strip())
                logger.warning(
                    "Ignoring invalid greeting_override; using Start-node greeting"
                )

        greeting_type = node.greeting_type or "text"

        if greeting_type == "audio" and node.greeting_recording_id:
            return ("audio", node.greeting_recording_id)

        if node.greeting:
            return ("text", self._format_prompt(node.greeting))

        return None

    def get_start_greeting(self) -> Optional[tuple[str, Optional[str]]]:
        """Return the greeting info for the start node, or None if not configured."""
        return self.get_node_greeting(self.active_agent.workflow.start_node_id)

    async def queue_node_opening(
        self,
        *,
        node_id: str,
        previous_node_id: Optional[str] = None,
        generate_if_no_greeting: bool = False,
        origin_visit_id: Optional[str] = None,
    ) -> Literal["none", "greeting", "llm"]:
        """Queue the opening behavior for a node.

        This is the shared source of truth for how a node begins once the
        engine is ready and the node has already been set on the context.

        Args:
            node_id: The node being opened.
            previous_node_id: The node just left. Passing the same id as
                ``node_id`` suppresses the configured greeting, which is how a
                destination agent continues an already-running conversation
                instead of introducing itself.
            generate_if_no_greeting: Ask the LLM for an opening turn when the
                node has no configured greeting.
            origin_visit_id: The agent visit this opening belongs to. An
                opening queued for a visit that is no longer running is
                dropped, so exactly one opening runs per activation.

        Returns:
            "greeting" when a text/audio greeting was queued,
            "llm" when an initial LLM generation was queued,
            "none" when nothing was queued.
        """
        agent = self._active_agent
        if origin_visit_id is not None and origin_visit_id != agent.visit_id:
            logger.debug(
                f"Dropping node opening from retired visit {origin_visit_id}; "
                f"{agent.visit_id} owns the call"
            )
            return "none"

        if previous_node_id != node_id:
            greeting_info = self.get_node_greeting(node_id)
            if greeting_info:
                greeting_type, greeting_value = greeting_info
                if (
                    greeting_type in {"audio", "audio_recording_id"}
                    and greeting_value
                    and self._fetch_recording_audio
                    and self._transport_output is not None
                ):
                    logger.debug(f"Playing audio greeting recording: {greeting_value}")
                    fetch_kwargs = (
                        {"recording_id": greeting_value}
                        if greeting_type == "audio_recording_id"
                        else {"recording_pk": int(greeting_value)}
                    )
                    result = await self._fetch_recording_audio(**fetch_kwargs)
                    if result:
                        await play_audio(
                            result.audio,
                            sample_rate=(
                                self._audio_config.pipeline_sample_rate
                                if self._audio_config
                                else 16000
                            ),
                            queue_frame=self._transport_output.queue_frame,
                            transcript=result.transcript,
                            append_to_context=True,
                        )
                        await self._open_realtime_after_recorded_greeting(
                            result.transcript
                        )
                        return "greeting"
                    logger.warning(
                        f"Failed to fetch audio greeting {greeting_value}, "
                        "falling back to LLM generation"
                    )
                elif greeting_value and agent.worker is not None:
                    logger.debug("Playing text greeting via TTS")
                    # append_to_context=True so the assistant aggregator commits
                    # the greeting to the LLM context once TTS finishes; without
                    # it the LLM would re-greet on its first generation.
                    await agent.speak(
                        greeting_value, append_to_context=True, persist_to_logs=False
                    )
                    return "greeting"

        if (
            generate_if_no_greeting
            and agent.llm is not None
            and self.context is not None
        ):
            logger.debug("Queueing initial LLM generation for node opening")
            # Queue after the voicemail detector in the live pipeline so the
            # detector can gate initial generations when needed.
            await agent.run_llm(self.context)
            return "llm"

        return "none"

    async def _open_realtime_after_recorded_greeting(
        self, transcript: str | None
    ) -> None:
        """Hand the opening turn to a realtime service after a recording.

        Realtime providers seed their session, and open their caller-audio
        gate, off the opening turn the engine gives them. A recorded greeting
        is queued straight to the output transport, so without this the
        session stays unseeded and Gemini Live discards every caller frame -
        the bot greets, then never answers.

        The transcript goes with it because the handoff happens while the
        recording is still playing: the assistant aggregator only commits it
        to ``LLMContext`` once playback drains, which is too late to be part
        of the provider's session seed. Waiting for that commit instead would
        leave the caller unheard for the length of the greeting, and an
        interruptible node does not mute them.

        A no-op for text LLMs, which hold no session and re-read the context
        on every generation.
        """
        handle_prerecorded_greeting = getattr(
            self.active_agent.llm, "handle_prerecorded_greeting", None
        )
        if handle_prerecorded_greeting is None:
            return
        await handle_prerecorded_greeting(self.context, transcript)

    async def _handle_end_node(self, node: Node) -> None:
        """Handle end node execution."""
        # Setup LLM context with prompts and functions.
        await self._setup_llm_context(node)

    async def _handle_agent_node(self, node: Node) -> None:
        """Handle agent node execution."""
        # Setup LLM context with prompts and functions.
        await self._setup_llm_context(node)

    def set_call_disposition(self, disposition: str) -> None:
        """Fix the call's disposition ahead of teardown.

        ``end_call_with_reason`` falls back to its own ``call_status`` only when
        no disposition has been recorded, so an outcome already known to be
        final before the pipeline winds down has to be stamped here.

        An external-PBX transfer is the case that needs it. The PBX pulls the
        customer off our media leg within ~100ms of its transfer API returning,
        so ``on_client_disconnected`` fires while the transfer handler is still
        waiting out its post-handoff settle delay. Whichever path reaches
        ``end_call_with_reason`` first wins the disposition and the other is a
        no-op, so without this the completed transfer is recorded as a user
        hangup.
        """
        self._gathered_context[CALL_DISPOSITION_CONTEXT_KEY] = disposition
        self._gathered_context["mapped_call_disposition"] = self.map_disposition(
            disposition
        )

    def refine_call_disposition(
        self,
        fallback_disposition: str,
        extracted_disposition: str | None,
    ) -> None:
        """Replace the mechanical fallback with a classified business outcome.

        ``end_call_with_reason`` initially copies ``call_status`` into
        ``call_disposition``. Only the dedicated disposition service may refine
        it; ordinary node extractions are deliberately not consulted.

        A no-op whenever the fallback changed while classification was running
        or the service returned no supported disposition.
        """
        if (
            not extracted_disposition
            or self._gathered_context.get(CALL_DISPOSITION_CONTEXT_KEY)
            != fallback_disposition
        ):
            return

        logger.debug(
            f"Refining call disposition: {fallback_disposition} -> "
            f"{extracted_disposition}"
        )
        self.set_call_disposition(extracted_disposition)
        self.record_call_tags([extracted_disposition])

    def map_disposition(self, disposition: str | None) -> str | None:
        """Translate ``disposition`` through the organization's mapping.

        Public because the external-PBX transfer path records the call's outcome
        on the PBX before it is stamped here -- it needs the same translation
        this engine will apply, and must not resolve it a second way.
        """
        return apply_disposition_mapping(self._disposition_mapping, disposition)

    def record_context(self, values: Mapping[str, object]) -> None:
        """Merge caller-supplied values into the call's gathered context.

        The engine owns this dict for the life of the call. Teardown handlers
        add to it through here rather than mutating a snapshot they took
        earlier, so there is one copy of the truth to read and persist at the
        end instead of several that have to be reconciled.
        """
        self._gathered_context.update(values)

    def record_call_tags(self, tags: Iterable[str] = ()) -> None:
        """Add call tags, along with any carried by ``tag_*`` context keys.

        A workflow author can name an extraction variable ``tag_something`` to
        turn its value into a call tag. Those arrive through the extraction
        merge, so promoting them belongs here, next to the tags the engine
        records itself. Idempotent, so teardown paths may call it more than
        once.
        """
        call_tags = self._gathered_context.setdefault("call_tags", [])
        promoted = [
            value
            for key, value in self._gathered_context.items()
            if key.startswith("tag_") and isinstance(value, str)
        ]
        for tag in (*tags, *promoted):
            if tag and tag not in call_tags:
                call_tags.append(tag)

    async def end_call_with_reason(
        self, call_status: str, abort_immediately: bool = False
    ):
        """Own teardown outside any child tool that retirement may cancel."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(
                self._end_call(call_status, abort_immediately), name="call-shutdown"
            )
        await asyncio.shield(self._shutdown_task)

    async def _end_call(
        self,
        call_status: str,
        abort_immediately: bool = False,
    ):
        """End the pipeline and record its status and business outcome.

        Args:
            call_status: Observable call-termination mechanism.
            abort_immediately: Queue a cancellation instead of a graceful end.
        """
        if self._call_disposed:
            logger.debug(f"Call already Disposed: {self._call_disposed}")
            return

        self._call_disposed = True

        # Mute the pipeline
        self._mute_pipeline = True

        # A handoff in flight is invalidated before anything else, so no later
        # phase can activate an agent into a call that is ending.
        coordinator = self.__dict__.get("_transfer_coordinator")
        if coordinator is not None:
            await coordinator.invalidate(call_status)

        # The call status is the observed termination mechanism. It is never
        # generated by an LLM.
        self._gathered_context[CALL_STATUS_CONTEXT_KEY] = call_status

        # Prefer a business outcome already recorded during the call -- by an
        # end-call tool or a transfer. Otherwise the mechanical status is the
        # disposition fallback.
        #
        # Stamped before the extraction below rather than after it, so that a
        # call always carries an outcome even when that extraction times out or
        # comes back with nothing. `refine_call_disposition` upgrades it once
        # the extraction has actually landed.
        recorded_disposition = self._gathered_context.get(
            CALL_DISPOSITION_CONTEXT_KEY, ""
        )
        should_extract_disposition = not bool(recorded_disposition)
        call_disposition = (
            recorded_disposition or self._gathered_context[CALL_STATUS_CONTEXT_KEY]
        )
        self._gathered_context[CALL_DISPOSITION_CONTEXT_KEY] = call_disposition
        # A dict lookup against the mapping loaded in `initialize`, not a DB
        # round trip -- see `_disposition_mapping`.
        self._gathered_context["mapped_call_disposition"] = self.map_disposition(
            call_disposition
        )

        # Tagged with the untranslated disposition. Tags are Dograh's own
        # vocabulary -- `user_speech`, `not_connected` -- and are what the
        # mapping-less view of a run is read from.
        self.record_call_tags([call_disposition])

        if call_status not in (
            EndTaskReason.PIPELINE_ERROR.value,
            EndTaskReason.VOICEMAIL_DETECTED.value,
            *ANSWER_TERMINAL_REASONS,
        ):
            # Finish ordinary node extraction, then classify the call outcome
            # independently when the mechanical status is only a fallback.
            # Bound both calls together so a stuck LLM cannot hold the call open.
            extracted_disposition = None
            try:
                async with asyncio.timeout(FINAL_EXTRACTION_TIMEOUT_SECONDS):
                    await self.perform_final_variable_extraction()
                    if (
                        should_extract_disposition
                        and self._disposition_extraction_service is not None
                    ):
                        extracted_disposition = (
                            await self._disposition_extraction_service.extract(
                                parent_context=self._get_otel_context(),
                                organization_id=self._organization_id,
                                workflow_run_id=self._workflow_run_id,
                            )
                        )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Final extraction did not finish within "
                    f"{FINAL_EXTRACTION_TIMEOUT_SECONDS}s; keeping the recorded "
                    f"disposition '{call_disposition}'"
                )
            if should_extract_disposition:
                self.refine_call_disposition(
                    call_disposition,
                    extracted_disposition,
                )

        # Agent workers are shut down before the call pipeline, and waited for.
        # A terminal frame queued below travels this worker's pipeline and
        # nothing else, so a child left running would be cancelled later by the
        # runner -- losing its last audio and its usage metrics -- instead of
        # draining into a transport that is still up.
        await self.retire_agents(call_status, drain=not abort_immediately)

        # One run row anchors the call, so which agents it visited, on which
        # definitions and models, is recorded on its gathered context. Written
        # after retirement so every visit carries its exit. Without this a
        # transferred call reports only the agent it started on.
        if self.__dict__.get("_agent_visits") or self.active_agent.is_child:
            self._gathered_context["agent_visits"] = self.agent_visits

        frame_to_push = (
            CancelFrame(reason=call_status)
            if abort_immediately
            else EndFrame(reason=call_status)
        )

        # No persist here. This used to write the gathered context before
        # queueing the frame, because the pipeline could be cancelled out from
        # under this coroutine and `on_pipeline_finished` would then write a
        # snapshot taken before the extraction landed. In-pipeline
        # cancellations now funnel back through this method (see
        # TerminationFunnelProcessor), so the frame below is what ends the
        # pipeline and `on_pipeline_finished` cannot run until it does -- one
        # write, of the finished context, is enough. Hangup strategies read
        # only keys recorded at call setup or at transfer time, never the
        # terminal extraction.
        logger.debug(
            f"Finishing run with call status: {call_status}, disposition: "
            f"{self._gathered_context.get(CALL_DISPOSITION_CONTEXT_KEY, call_disposition)} "
            f"queueing frame {frame_to_push}"
        )
        await self.call_worker.queue_frame(frame_to_push)

    async def queue_text_message(
        self, text: str, *, append_to_context: bool = False, mute_user: bool = False
    ) -> bool:
        """Queue edge/tool speech only when the pipeline has a TTS service.

        Realtime services own their responses. Skip before arming mute or
        reporting queued playback: discarded text cannot produce a completion
        event to release either wait. Opening greetings use a separate path.
        """
        if self._is_realtime:
            logger.debug("Skipping configured text speech in realtime mode")
            return False
        if mute_user:
            self._queued_speech_mute_state = "waiting"
        # Spoken by whichever agent is running, in its own voice: a transfer
        # announcement has to come from the agent saying goodbye, not from
        # whoever happens to own the transport.
        await self._active_agent.speak(
            text, append_to_context=append_to_context, persist_to_logs=True
        )
        return True

    def clear_queued_speech_mute(self) -> None:
        """Release a mute taken for speech that never reached the caller.

        ``queue_text_message(mute_user=True)`` mutes until the speech starts
        and stops playing. Speech that is never spoken -- a TTS that failed,
        a handoff that gave up on waiting -- produces neither event, so
        without this the caller stays muted for the rest of the call.
        """
        if self._queued_speech_mute_state != "idle":
            logger.debug("Releasing queued-speech mute for speech that never played")
            self._queued_speech_mute_state = "idle"

    def arm_speech_playback(self) -> None:
        """Start tracking the next piece of speech queued to the transport.

        Call this immediately *before* queueing a TTSSpeakFrame (or raw
        recording audio) whose playback a later step must wait for. Any speech
        already in flight is ignored: the tracker only completes once a fresh
        BotStartedSpeakingFrame has been followed by a BotStoppedSpeakingFrame.
        """
        self._speech_playback_started.clear()
        self._speech_playback_finished.clear()

    async def wait_for_speech_playback(
        self, *, start_timeout: float = 5.0, playback_timeout: float = 30.0
    ) -> bool:
        """Wait for speech armed via ``arm_speech_playback`` to finish playing.

        Speech queued to the pipeline only reaches the caller once the output
        transport has written it out in real time, so anything that tears the
        audio path down (a PBX transfer, a hangup) must wait for this first.

        Args:
            start_timeout: Seconds to wait for playback to begin. TTS can fail
                or return nothing, so a message that never starts must not
                block the caller indefinitely.
            playback_timeout: Seconds to wait for playback to complete once it
                has begun.

        Returns:
            True if the speech played to completion, False if either wait timed
            out (the caller should carry on regardless).
        """
        try:
            await asyncio.wait_for(
                self._speech_playback_started.wait(), timeout=start_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Queued speech never started playing within {start_timeout}s; "
                "continuing without it"
            )
            return False

        try:
            await asyncio.wait_for(
                self._speech_playback_finished.wait(), timeout=playback_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Queued speech did not finish playing within {playback_timeout}s; "
                "continuing"
            )
            return False

        return True

    async def should_mute_user(self, frame: "Frame") -> bool:
        """
        Callback for CallbackUserMuteStrategy to determine if the user should be muted.

        This method tracks bot speaking state from frames and mutes the user when:
        - The pipeline is being shut down (_mute_pipeline is True), OR
        - The bot is speaking AND the current node has allow_interrupt=False

        Returns:
            True if the user should be muted, False otherwise.
        """
        # Track bot speaking state from frames
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_is_speaking = True
            if self._queued_speech_mute_state == "waiting":
                self._queued_speech_mute_state = "playing"
            self._speech_playback_started.set()
            self._speech_playback_finished.clear()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            self._queued_speech_mute_state = "idle"
            self._speech_playback_finished.set()

        # Always mute if pipeline is shutting down
        if self._mute_pipeline:
            return True

        # Mute while queued speech (transition/tool message) is pending or playing
        if self._queued_speech_mute_state != "idle":
            return True

        # Mute if bot is speaking and current node doesn't allow interruption
        if self._bot_is_speaking and self.active_agent.current_node:
            # If we should not allow interruption, mute the pipeline
            if not self.active_agent.current_node.allow_interrupt:
                return True

        return False

    def create_user_idle_handler(self):
        """
        Returns a UserIdleHandler that manages user-idle timeouts with state.
        The handler tracks retry count and handles escalating prompts.
        """
        return engine_callbacks.create_user_idle_handler(self)

    def create_max_duration_callback(self):
        """
        This callback is called when the call duration exceeds the max duration.
        We use this to send the EndTaskFrame.
        """
        return engine_callbacks.create_max_duration_callback(self)

    def create_generation_started_callback(self, visit_id: Optional[str] = None):
        """
        This callback is called when a new generation starts.
        This is used to reset the flags that control the flow of the engine.

        Args:
            visit_id: The agent visit whose generation stage this belongs to.
                A retired agent finishing its last generation must not reset
                the reference text the running agent is building.
        """
        return engine_callbacks.create_generation_started_callback(
            self, visit_id=visit_id
        )

    def create_llm_text_frame_callback(self, visit_id: Optional[str] = None):
        """Return the text accumulator for one agent visit's generation stage."""

        async def accumulate(text: str) -> None:
            await self.handle_llm_text_frame(text, visit_id=visit_id)

        return accumulate

    def owns_generation(self, visit_id: Optional[str]) -> bool:
        """Whether ``visit_id`` is the agent currently owning the call."""
        return visit_id is None or visit_id == self._active_agent.visit_id

    def create_aggregation_correction_callback(self) -> Callable[[str], str]:
        """Create a callback that corrects corrupted aggregation using reference text."""
        return engine_callbacks.create_aggregation_correction_callback(self)

    def set_context(self, context: LLMContext) -> None:
        """Set the LLM context.

        This allows setting the context after the engine has been created,
        which is useful when the context needs to be created after the engine.
        """
        self.context = context

    # ------------------------------------------------------------------
    # Agent lifecycle
    #
    # The engine alone authorizes agent activation, context replacement and
    # call completion. `AgentTransferCoordinator` sequences a handoff; every
    # step that changes who owns the call comes back through here.
    # ------------------------------------------------------------------

    def set_agent_factory(self, factory) -> None:
        """Enable in-call agent transfer by supplying a runtime factory."""
        self._agent_factory = factory

    @property
    def agent_transfer_enabled(self) -> bool:
        """Whether this call can hand the caller to another agent.

        True for every cascade call. False only in realtime, where the tool
        still registers but refuses, so the agent tells the caller rather than
        failing silently.
        """
        return self.__dict__.get("_agent_factory") is not None

    @property
    def transfer_coordinator(self):
        """The call's handoff coordinator, created on first use."""
        if self._transfer_coordinator is None:
            from api.services.workflow.agent_transfer import AgentTransferCoordinator

            self._transfer_coordinator = AgentTransferCoordinator(self)
        return self._transfer_coordinator

    @property
    def transfer_in_progress(self) -> bool:
        """Whether a handoff is running, so ordinary prompting must hold off."""
        coordinator = self.__dict__.get("_transfer_coordinator")
        return coordinator is not None and coordinator.in_progress

    @property
    def agent_visits(self) -> list[dict]:
        """One record per agent visit, oldest first, including the live one."""
        return [*self.__dict__.get("_agent_visits", []), self.active_agent.describe()]

    @property
    def hold_audio_sample_rate(self) -> int:
        """Sample rate for ringer audio queued straight to the transport."""
        if self._audio_config:
            return self._audio_config.transport_out_sample_rate
        return 8000

    @property
    def transport_output_queue_frame(self):
        """Frame sink that reaches the caller without passing through STT."""
        return self._transport_output.queue_frame

    async def start_initial_agent(self, timeout: float = 10.0) -> bool:
        """Attach and activate the agent the call starts on.

        Runs once the call pipeline is up, so the agent's worker joins the
        call's conversation trace and the runner is there to start it. A
        no-op when the first agent runs in the call worker itself.
        """
        agent = self._active_agent
        if not agent.is_child:
            return True
        if self._agent_factory is None or self._call_worker is None:
            logger.error("Cannot start the initial agent without a factory")
            return False

        await self._agent_factory.attach(agent)
        if not await agent.wait_until_started(timeout=timeout):
            return False
        return await self.activate_agent(agent, timeout=timeout)

    async def build_agent(self, *, workflow_id: int, visit_id: str) -> AgentRuntime:
        """Build a destination agent and attach it to the call, inactive."""
        if self._agent_factory is None:
            from api.services.pipecat.agent_runtime_factory import AgentBuildError

            raise AgentBuildError(
                "transfer_unavailable",
                "This call was not set up to transfer between agents",
            )
        runtime = await self._agent_factory.build(
            workflow_id=workflow_id, visit_id=visit_id
        )
        self._pending_agent = runtime
        return runtime

    async def prepare_agent(self, runtime: AgentRuntime) -> None:
        await self._open_mcp_sessions(runtime)
        node = runtime.workflow.nodes[runtime.workflow.start_node_id]
        await self._prepare_node(runtime, node)
        runtime.current_node = node

    def commit_agent(self, runtime: AgentRuntime, snapshot) -> None:
        """The only handoff commit point. No awaits and no provider work."""
        from api.services.workflow.agent_handoff_context import messages_after_boundary

        tail = messages_after_boundary(self.context, snapshot.boundary)
        self.context.set_messages([*snapshot.messages, *tail])
        self.context.set_tools(runtime.tools)
        self.context.set_otel_span_name(f"llm-{runtime.current_node.name}")
        runtime.entered_at = time.time()
        self.install_agent(runtime, previous=self.active_agent)
        self._custom_tool_manager = CustomToolManager(self, runtime)
        self._agent_on_hold = False
        nodes = self._gathered_context.setdefault("nodes_visited", [])
        if runtime.current_node.name not in nodes:
            nodes.append(runtime.current_node.name)
        logger.info(
            f"[transfer] installed {runtime.visit_id}: "
            f"{len(snapshot.messages)} handoff messages (summarized={snapshot.summarized}) "
            f"+ {len(tail)} live messages"
        )

    async def notify_agent_entered(self, runtime: AgentRuntime) -> None:
        node = runtime.current_node
        if self._node_transition_callback:
            try:
                await self._node_transition_callback(
                    node.id, node.name, None, None, node.allow_interrupt
                )
            except Exception as error:
                logger.debug(f"Failed to send agent transition event: {error}")

    @property
    def selected_visit_id(self) -> str | None:
        return None if self._agent_on_hold else self.active_agent.visit_id

    def agent_can_act(self, runtime: AgentRuntime) -> bool:
        return not self._call_disposed and self.selected_visit_id == runtime.visit_id

    def install_agent(
        self,
        runtime: AgentRuntime,
        *,
        previous: Optional[AgentRuntime] = None,
        previous_exit_reason: str = "transferred",
    ) -> None:
        """Make ``runtime`` the agent that owns the call.

        Closes the previous visit into the call's history rather than
        discarding it: usage, nodes visited and outcome are per visit and are
        all reported at the end of the call. The exit is stamped here, before
        the history entry is taken, so the record is not a snapshot of a visit
        that had not ended yet.
        """
        if previous is not None and previous is not runtime:
            previous.exited_at = previous.exited_at or time.time()
            previous.exit_reason = previous.exit_reason or previous_exit_reason
            self._agent_visits.append(previous.describe())
            self._retired_agents.append(previous)
        self._active_agent = runtime
        self._pending_agent = None

    def discard_pending_agent(self, runtime: AgentRuntime) -> None:
        """Forget a destination that was prepared but never took the call.

        Its worker is released by the caller; this drops the engine's handle
        so `pending_agent` does not keep reporting a retired agent, and
        teardown does not try to shut it down a second time.
        """
        if self._pending_agent is runtime:
            self._pending_agent = None

    async def activate_agent(self, runtime: AgentRuntime, *, timeout: float) -> bool:
        """Let ``runtime`` back into the conversation and confirm it took.

        An inactive worker is handed no frames from the bus, so activation is
        what actually connects an agent to the caller. It only takes effect
        once the worker has started, which is why this confirms rather than
        assuming the message was enough.
        """
        if not runtime.is_child or self._call_worker is None:
            return True

        await self._call_worker.activate_worker(runtime.worker.name)

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if runtime.worker.active:
                logger.debug(f"Agent visit {runtime.visit_id} activated")
                return True
            await asyncio.sleep(0.01)
        logger.warning(
            f"Agent visit {runtime.visit_id} did not activate within {timeout}s"
        )
        return False

    async def deactivate_agent(self, runtime: AgentRuntime) -> None:
        """Take ``runtime`` out of the conversation without ending it.

        Gates inference structurally: the agent stops receiving context frames
        while recognition, recording and the call timer carry on. Reversible --
        a rolled-back handoff activates the same worker again.
        """
        if not runtime.is_child or self._call_worker is None:
            return
        self._agent_on_hold = True
        await self._call_worker.deactivate_worker(runtime.worker.name)

    async def drain_call_pipeline(self, timeout: float = 5.0) -> bool:
        """Wait for everything in flight to reach the caller.

        The flush probe travels through the selected agent's worker and back, so
        this drains the agent's generation stage as well as the call pipeline.
        During hold it stays on the call pipeline and drains queued ring audio.
        """
        if self._call_worker is None:
            return False
        return await self._call_worker.flush_pipeline(timeout=timeout)

    async def pause_background_context_writers(self) -> None:
        """Stop anything that would mutate the conversation behind a handoff."""
        if self._context_summarization_manager:
            await self._context_summarization_manager.cleanup()
        await self._await_pending_extractions()

    def record_transfer_outcome(self, record: dict) -> None:
        """Record how one handoff attempt ended, for the call's run record."""
        self._transfer_outcomes.append(record)
        self._gathered_context["agent_transfers"] = list(self._transfer_outcomes)

    async def handle_agent_error(self, runtime: AgentRuntime, frame) -> None:
        """Decide what one agent's pipeline error means for the call.

        The call pipeline's termination funnel treats a terminal error as the
        end of the call, which is right only for the agent the caller is
        actually talking to. An agent still being prepared, or one already
        handed over, fails its own transfer and leaves the call alone.
        """
        from api.services.pipecat.termination_funnel_processor import (
            is_terminal_error,
        )

        if not is_terminal_error(frame):
            return

        runtime.error = getattr(frame, "error", None) or "pipeline error"
        if runtime is not self._active_agent:
            logger.warning(
                f"Agent visit {runtime.visit_id} is unusable ({runtime.error}); "
                "the call continues with the agent that owns it"
            )
            return

        logger.error(
            f"Agent visit {runtime.visit_id} is unusable ({runtime.error}); "
            "ending the call"
        )
        await self.end_call_with_reason(EndTaskReason.PIPELINE_ERROR.value)

    async def retire_agents(self, reason: str, *, drain: bool = True) -> None:
        """Shut down every agent worker this call created.

        Terminal frames queued on the call worker drain the call pipeline
        only: child propagation runs off a bus message that a frame on the
        push queue never produces. Without this the runner eventually cancels
        the orphans at shutdown, which cuts their final audio and metrics
        instead of draining them.

        Args:
            reason: Recorded as each visit's exit reason.
            drain: Let each agent finish what it is saying. False on the abort
                path, where the caller is already gone or the call is over its
                hard limit and waiting would only delay teardown.
        """
        agents = [
            self.active_agent,
            self.pending_agent,
            *self.__dict__.get("_retired_agents", []),
        ]
        for agent in agents:
            if agent is None or not agent.is_child:
                continue
            agent.exited_at = agent.exited_at or time.time()
            agent.exit_reason = agent.exit_reason or reason
            if drain:
                await agent.retire(reason)
            else:
                await agent.abort(reason)

    def set_audio_config(self, audio_config) -> None:
        """Set the audio configuration for the pipeline."""
        self._audio_config = audio_config

    def set_transport_output(self, transport_output) -> None:
        """Set the transport output processor for direct audio playback.

        Audio queued here bypasses STT and the rest of the pipeline,
        going straight to the caller.
        """
        self._transport_output = transport_output

    def set_fetch_recording_audio(self, fetch_fn) -> None:
        """Set the recording audio fetcher callback."""
        self._fetch_recording_audio = fetch_fn

    def set_mute_pipeline(self, mute: bool) -> None:
        """Set the pipeline mute state.

        This controls whether user input should be muted via the CallbackUserMuteStrategy.
        When muted, the user's audio input will be blocked.

        Args:
            mute: True to mute user input, False to allow input
        """
        logger.debug(f"Setting pipeline mute state to: {mute}")
        self._mute_pipeline = mute

    async def handle_llm_text_frame(self, text: str, visit_id: Optional[str] = None):
        """Accumulate LLM text frames to build reference text.

        The reference text corrects the assistant aggregator's transcript, and
        that aggregator is call-scoped, so text from an agent that no longer
        owns the call is dropped rather than mixed into the running agent's.
        """
        if not self.owns_generation(visit_id):
            return
        self._current_llm_generation_reference_text += text

    def is_call_disposed(self):
        """Check whether a call has been disposed by the engine"""
        return self._call_disposed

    async def get_gathered_context(self) -> dict:
        """Read the call's gathered context.

        A copy, so a caller cannot edit the engine's state by accident: writes
        go through ``record_context`` / ``record_call_tags`` / the disposition
        recorders. Still shallow -- nested values are shared -- so treat the
        result as read-only rather than as an isolated snapshot.
        """
        return self._gathered_context.copy()

    async def _open_mcp_sessions(self, agent: AgentRuntime | None = None) -> None:
        """Connect every MCP-category tool referenced by any workflow node.
        Failures degrade (session marked unavailable); never raises."""
        from api.services.workflow.tools.mcp_tool import (
            McpDefinitionError,
            validate_mcp_definition,
        )

        agent = agent or self.active_agent
        try:
            tool_uuids: set[str] = set()
            for node in agent.workflow.nodes.values():
                for tu in getattr(node, "tool_uuids", None) or []:
                    tool_uuids.add(tu)
            if not tool_uuids:
                return

            organization_id = await self._get_organization_id()
            if not organization_id:
                logger.warning("Cannot open MCP sessions: organization_id missing")
                return

            tools = await db_client.get_tools_by_uuids(
                list(tool_uuids), organization_id
            )
            for tool in tools:
                if tool.category != ToolCategory.MCP.value:
                    continue
                try:
                    cfg = validate_mcp_definition(tool.definition)
                except McpDefinitionError as e:
                    logger.warning(
                        f"Skipping MCP tool '{tool.name}' ({tool.tool_uuid}): "
                        f"invalid definition: {e}"
                    )
                    continue

                credential = None
                if cfg["credential_uuid"]:
                    try:
                        credential = await db_client.get_credential_by_uuid(
                            cfg["credential_uuid"], organization_id
                        )
                    except Exception as e:
                        logger.warning(
                            f"MCP tool '{tool.name}': credential fetch failed: {e}"
                        )
                        continue

                session = McpToolSession(
                    tool_uuid=tool.tool_uuid,
                    tool_name=tool.name,
                    url=cfg["url"],
                    credential=credential,
                    tools_filter=cfg["tools_filter"],
                    timeout_secs=cfg["timeout_secs"],
                    sse_read_timeout_secs=cfg["sse_read_timeout_secs"],
                )
                agent.mcp_sessions[tool.tool_uuid] = session
                await session.start_managed()
        except Exception as e:
            logger.warning(
                f"Failed to open MCP sessions; call proceeds without MCP tools: {e}",
                exc_info=True,
            )

    async def close_mcp_sessions(self) -> None:
        """Release connection owners, including on early call startup failure."""
        for agent in [self.active_agent, self.pending_agent, *self._retired_agents]:
            if agent is not None:
                await agent.close_mcp_sessions()

    async def cleanup(self):
        """Clean up engine resources on disconnect.

        Connection owners are finalized by close_mcp_sessions() in the run
        finally block, including failures before the pipeline starts.
        """
        # Cancel any pending timeout tasks
        if (
            self._user_response_timeout_task
            and not self._user_response_timeout_task.done()
        ):
            self._user_response_timeout_task.cancel()

        # Cancel any in-flight background summarization.
        if self._context_summarization_manager:
            await self._context_summarization_manager.cleanup()
