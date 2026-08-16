from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Literal,
    Mapping,
    Optional,
    Union,
)

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallResultProperties,
    LLMContextFrame,
    TTSSpeakFrame,
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
from api.services.pipecat.audio_playback import play_audio
from api.services.workflow.workflow_graph import Node, WorkflowGraph

if TYPE_CHECKING:
    from pipecat.frames.frames import Frame
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.utils.tracing.tracing_context import TracingContext

    from api.services.integrations.base import IntegrationCallCapabilities

    LLMService = Union[OpenAILLMService, AnthropicLLMService, GoogleLLMService]

import asyncio

from loguru import logger

from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY
from api.services.workflow import pipecat_engine_callbacks as engine_callbacks
from api.services.workflow.disposition_extraction import (
    CALL_DISPOSITION_VARIABLE,
    CALL_STATUS_CONTEXT_KEY,
    END_REASON_CONTEXT_KEY,
    build_disposition_variable,
    coerce_disposition,
    prepare_extraction_variables,
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

# Gathered-context keys the engine records itself. Variable extraction merges
# its results into the same dict, so these are held back from that merge.
#
# The call disposition is recorded only by the engine, which keeps its mapped
# counterpart in sync for reporting, filters and external-PBX write-backs.
_ENGINE_OWNED_CONTEXT_KEYS = frozenset(
    {
        "call_disposition",
        "mapped_call_disposition",
        CALL_STATUS_CONTEXT_KEY,
        END_REASON_CONTEXT_KEY,
        "call_tags",
    }
)

# How long the terminal extraction gets before the call is disposed of without
# it. In-pipeline cancellations funnel through `end_call_with_reason`, so this
# coroutine is now the only thing that ends the pipeline: an LLM that never
# answers would otherwise hold the call, its telephony channel and every
# service behind it open indefinitely. Measured on the abrupt-hangup path at
# p50 1.4s / p90 4.0s / max 21.3s, so this cuts off the tail and nothing else.
FINAL_EXTRACTION_TIMEOUT_SECONDS = 8.0


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
        context_compaction_enabled: bool = False,
        run_transition_variable_extraction_in_background: bool = True,
        call_disposition_prompt: str | None = None,
    ):
        self.task = task
        self.llm = llm
        # LLM used for out-of-band inference (variable extraction, context
        # summarization). Falls back to the pipeline LLM when not provided.
        # In realtime mode the pipeline LLM is a speech-to-speech service
        # that does not implement run_inference, so a separate text LLM
        # must be passed in.
        self.inference_llm = inference_llm or llm
        # Variable extraction can use a separately tagged managed-model client
        # without rerouting normal conversation or context-summarization calls.
        self.variable_extraction_llm = variable_extraction_llm or self.inference_llm
        self.context = context
        self.workflow = workflow
        self._call_context_vars = call_context_vars
        self._workflow_run_id = workflow_run_id
        self._node_transition_callback = node_transition_callback
        self._run_transition_variable_extraction_in_background = (
            run_transition_variable_extraction_in_background
        )
        self._call_disposition_prompt = call_disposition_prompt
        self._initialized = False
        self._call_disposed = False
        self._current_node: Optional[Node] = None
        self._gathered_context: dict = {}
        self._user_response_timeout_task: Optional[asyncio.Task] = None
        self._pending_extraction_tasks: set[asyncio.Task] = set()
        # True once terminal call disposal has run its synchronous extraction.
        # Recoverable operations such as a failed transfer use a repeatable
        # flush and must not consume this one-shot finalization state.
        self._final_extraction_done: bool = False
        self._final_extraction_result: Optional[dict] = None

        # Will be set later in initialize() when we have
        # access to _context
        self._variable_extraction_manager = None

        # Track current LLM reference text for TTS aggregation correction
        self._current_llm_generation_reference_text: str = ""

        # Controls whether user input should be muted
        self._mute_pipeline: bool = False

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
        # two places that stamp a disposition -- record_call_disposition and
        # end_call_with_reason -- run on the teardown path, where a DB round
        # trip cannot be afforded (see end_call_with_reason). Empty means
        # "no mapping", which is the identity translation.
        self._disposition_mapping: dict[str, str] = {}

        # Open MCP tool sessions for this call, keyed by tool_uuid
        self._mcp_sessions: Dict[str, McpToolSession] = {}

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

        # Contributions from enabled integrations, set after construction by
        # run_pipeline once the integration runtime context exists.
        self._integration_capabilities: list["IntegrationCallCapabilities"] = []

        # Background context summarization on node transitions
        self._context_compaction_enabled: bool = context_compaction_enabled
        self._context_summarization_manager: Optional[ContextSummarizationManager] = (
            None
        )

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
            self.task, "_tracing_context", None
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

        if functions:
            tools_schema = ToolsSchema(standard_tools=functions)
            self.context.set_tools(tools_schema)

        # For Gemini Live, set context on the LLM before _update_settings so that
        # _connect (triggered by reconnect) can read tools from it.
        if hasattr(self.llm, "_context") and not self.llm._context and self.context:
            self.llm._context = self.context

        await self.llm._update_settings(LLMSettings(system_instruction=system_prompt))

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
    ):
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
                    self._current_node,
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
                            sample_rate=self._audio_config.pipeline_sample_rate
                            if self._audio_config
                            else 16000,
                            queue_frame=self._transport_output.queue_frame,
                            transcript=result.transcript,
                            persist_to_logs=True,
                        )
                    else:
                        logger.warning(
                            f"Failed to fetch transition audio {transition_speech_recording_id}"
                        )
                elif transition_speech:
                    logger.info(f"Playing transition speech: {transition_speech}")
                    self._queued_speech_mute_state = "waiting"
                    await self.task.queue_frame(
                        TTSSpeakFrame(
                            transition_speech,
                            append_to_context=False,
                            persist_to_logs=True,
                        )
                    )

                # Set context for the new node, so that when the function call result
                # frame is received by LLMContextAggregator and an LLM generation
                # is done, we have updated context and functions
                await self.set_node(transition_to_node)

                async def on_context_updated() -> None:
                    """
                    pipecat framework will run this function after the function call result has been updated in the context.
                    This way, when we do set_node from within this function, and go for LLM completion with updated
                    system prompts, the context is updated with function call result.
                    """
                    # FIXME: There is a potential race condition, when we generate LLM Completion from UserContextAggregator
                    # with FunctionCallResultFrame and we call end_call_with_reason where we queue EndFrame or CancelFrame.
                    # If EndFrame reaches the LLM Processor before the ContextFrame, we might never run generation which
                    # might be intended

                    # Queue EndFrame if we just transitioned to EndNode
                    if self._current_node.is_end:
                        await self.end_call_with_reason(EndTaskReason.END_CALL.value)

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

        return transition_func

    async def _register_transition_function_with_llm(
        self,
        name: str,
        transition_to_node: str,
        transition_speech: Optional[str] = None,
        transition_speech_type: Optional[str] = None,
        transition_speech_recording_id: Optional[str] = None,
    ):
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
        )

        # Register function with LLM
        self.llm.register_function(
            name,
            transition_func,
            is_node_transition=True,
        )

    async def _register_knowledge_base_function(
        self, document_uuids: list[str]
    ) -> None:
        """Register knowledge base retrieval function with the LLM.

        Args:
            document_uuids: List of document UUIDs to filter the search by
        """
        logger.debug(
            f"Registering knowledge base retrieval function with {len(document_uuids)} document(s)"
        )

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
        self.llm.register_function("retrieve_from_knowledge_base", retrieve_kb_func)

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
        node_variables = prepare_extraction_variables(
            node.extraction_variables,
        )
        if not node_variables:
            logger.debug(
                f"No LLM-derived variables configured for node: {node.name}; "
                "skipping variable extraction"
            )
            return None
        extraction_variables = [
            v.model_copy(update={"prompt": self._format_prompt(v.prompt)})
            if v.prompt
            else v
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
                        if key not in _ENGINE_OWNED_CONTEXT_KEYS
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
                metadata = failure_metadata_for_processor(self.variable_extraction_llm)
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
            self._current_node,
            run_in_background=False,
        )

    async def _perform_final_disposition_extraction(self) -> Optional[dict]:
        """Derive the configured call-level outcome once the conversation ends."""
        if not self._call_disposition_prompt or not self._variable_extraction_manager:
            return None
        if not self._variable_extraction_manager.has_user_turns():
            logger.debug(
                "No user speech in the conversation; skipping disposition extraction"
            )
            return None

        try:
            variable = build_disposition_variable(
                self._format_prompt(self._call_disposition_prompt)
            )
            return await self._variable_extraction_manager._perform_extraction(
                [variable], self._get_otel_context()
            )
        except Exception as e:
            # A disposition is best-effort. The mechanism recorded before this
            # request is the safe fallback when the inference provider fails.
            metadata = failure_metadata_for_processor(self.variable_extraction_llm)
            log_failure(
                classify_exception(
                    e,
                    source=metadata.source,
                    provider=metadata.provider,
                    error_owner=metadata.error_owner,
                ),
                organization_id=self._organization_id,
                workflow_run_id=self._workflow_run_id,
                node_name="call_disposition",
            )
            return None

    async def perform_final_variable_extraction(
        self, *, extract_disposition: bool
    ) -> Optional[dict]:
        """Perform the one-shot extraction used during call disposal.

        Awaits any background extractions still running from previous nodes,
        then runs the current node's extraction inline. Idempotency prevents
        duplicate terminal extraction when multiple teardown paths converge.

        The dedicated disposition extraction is independent from ordinary node
        variables and only runs when teardown has a fallback outcome to refine.
        """
        if self._final_extraction_done:
            logger.debug("Final variable extraction already performed; skipping")
            return self._final_extraction_result
        await self.flush_variable_extraction()
        self._final_extraction_result = (
            await self._perform_final_disposition_extraction()
            if extract_disposition
            else None
        )
        self._final_extraction_done = True
        return self._final_extraction_result

    async def _setup_llm_context(self, node: Node) -> None:
        """Common method to set up LLM context"""
        # Set OTel span name for tracing
        try:
            self.context.set_otel_span_name(f"llm-{node.name}")
        except AttributeError:
            logger.warning(f"context has no set_otel_span_name method")

        # Register transition functions if not an end node
        if not node.is_end:
            for outgoing_edge in node.out_edges:
                await self._register_transition_function_with_llm(
                    outgoing_edge.get_function_name(),
                    outgoing_edge.target,
                    outgoing_edge.transition_speech,
                    outgoing_edge.data.transition_speech_type,
                    outgoing_edge.data.transition_speech_recording_id,
                )

        # Register custom tool handlers for this node
        if node.tool_uuids and self._custom_tool_manager:
            await self._custom_tool_manager.register_handlers(
                node.tool_uuids,
                mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
            )

        # Register knowledge base retrieval handler if node has documents
        if node.document_uuids:
            await self._register_knowledge_base_function(node.document_uuids)

        # Compose prompt and functions via the context composer module
        system_prompt = compose_system_prompt_for_node(
            node=node,
            workflow=self.workflow,
            format_prompt=self._format_prompt,
            has_recordings=self._has_recordings,
            integration_addenda=self._resolve_integration_addenda(),
        )
        functions = await compose_functions_for_node(
            node=node,
            custom_tool_manager=self._custom_tool_manager,
        )
        await self._update_llm_context(system_prompt, functions)

    async def set_node(self, node_id: str, emit_transition_event: bool = True):
        """
        Simplified set_node implementation according to v2 PRD.
        """
        node = self.workflow.nodes[node_id]

        logger.debug(
            f"Executing node: name: {node.name} allow_interrupt: {node.allow_interrupt} is_end: {node.is_end}"
        )

        # Track previous node for transition event
        previous_node_name = self._current_node.name if self._current_node else None
        previous_node_id = self._current_node.id if self._current_node else None

        # Set current node for all nodes (including static ones) so STT mute filter works
        self._current_node = node

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
            self._context_summarization_manager.start()

    async def _handle_start_node(self, node: Node) -> None:
        """Handle start node execution."""
        # Check if delayed start is enabled
        if node.delayed_start:
            # Use configured duration or default to 3 seconds
            delay_duration = node.delayed_start_duration or 2.0
            logger.debug(
                f"Delayed start enabled - waiting {delay_duration} seconds before speaking"
            )
            await asyncio.sleep(delay_duration)

        # Setup LLM context with prompts and functions.
        await self._setup_llm_context(node)

    def get_node_greeting(self, node_id: str) -> Optional[tuple[str, Optional[str]]]:
        """Return the greeting info for a node, or None if not configured.

        Returns:
            A tuple of (greeting_type, value) where:
            - ("text", rendered_text) for text greetings spoken via TTS
            - ("audio", recording primary key) for configured audio greetings
            - ("audio_recording_id", recording ID) for call-level audio overrides
            Or None if no greeting is configured.
        """
        node = self.workflow.nodes.get(node_id)
        if not node:
            return None

        # A programmatic override applies only to the workflow entry greeting;
        # greetings on later nodes continue to use their saved configuration.
        if node.is_start:
            override = self._call_context_vars.get(GREETING_OVERRIDE_CONTEXT_KEY)
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
        return self.get_node_greeting(self.workflow.start_node_id)

    async def queue_node_opening(
        self,
        *,
        node_id: str,
        previous_node_id: Optional[str] = None,
        generate_if_no_greeting: bool = False,
    ) -> Literal["none", "greeting", "llm"]:
        """Queue the opening behavior for a node.

        This is the shared source of truth for how a node begins once the
        engine is ready and the node has already been set on the context.

        Returns:
            "greeting" when a text/audio greeting was queued,
            "llm" when an initial LLM generation was queued,
            "none" when nothing was queued.
        """
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
                            sample_rate=self._audio_config.pipeline_sample_rate
                            if self._audio_config
                            else 16000,
                            queue_frame=self._transport_output.queue_frame,
                            transcript=result.transcript,
                            append_to_context=True,
                        )
                        return "greeting"
                    logger.warning(
                        f"Failed to fetch audio greeting {greeting_value}, "
                        "falling back to LLM generation"
                    )
                elif greeting_value and self.task is not None:
                    logger.debug("Playing text greeting via TTS")
                    # append_to_context=True so the assistant aggregator commits
                    # the greeting to the LLM context once TTS finishes; without
                    # it the LLM would re-greet on its first generation.
                    await self.task.queue_frame(
                        TTSSpeakFrame(greeting_value, append_to_context=True)
                    )
                    return "greeting"

        if (
            generate_if_no_greeting
            and self.llm is not None
            and self.context is not None
        ):
            logger.debug("Queueing initial LLM generation for node opening")
            # Queue after the voicemail detector in the live pipeline so the
            # detector can gate initial generations when needed.
            await self.llm.queue_frame(LLMContextFrame(self.context))
            return "llm"

        return "none"

    async def _handle_end_node(self, node: Node) -> None:
        """Handle end node execution."""
        # Setup LLM context with prompts and functions.
        await self._setup_llm_context(node)

    async def _handle_agent_node(self, node: Node) -> None:
        """Handle agent node execution."""
        # Setup LLM context with prompts and functions.
        await self._setup_llm_context(node)

    def record_call_disposition(self, disposition: str) -> None:
        """Fix the call's disposition ahead of teardown.

        ``end_call_with_reason`` falls back to its own ``reason`` only when no
        disposition has been recorded, so an outcome already known to be final
        before the pipeline winds down has to be stamped here.

        An external-PBX transfer is the case that needs it. The PBX pulls the
        customer off our media leg within ~100ms of its transfer API returning,
        so ``on_client_disconnected`` fires while the transfer handler is still
        waiting out its post-handoff settle delay. Whichever path reaches
        ``end_call_with_reason`` first wins the disposition and the other is a
        no-op, so without this the completed transfer is recorded as a user
        hangup.
        """
        self._gathered_context["call_disposition"] = disposition
        self._gathered_context["mapped_call_disposition"] = self.map_disposition(
            disposition
        )

    def refine_call_disposition(
        self,
        fallback_disposition: str,
        final_extraction: Optional[Mapping[str, object]],
    ) -> None:
        """Replace the mechanical fallback with the terminal extraction outcome.

        ``end_call_with_reason`` initially copies ``call_status`` into
        ``call_disposition``. Only a result returned by the current node's final
        extraction may refine it; earlier node extractions are deliberately not
        consulted.

        Called after the extraction and before the persist, so the refined value
        is what gets written and what the external-PBX write-back later reads.
        A no-op whenever anything is missing: the floor is always a valid
        answer, and on the abrupt-hangup path the extraction may well have been
        killed with the rest of the pipeline.
        """
        if self._gathered_context.get("call_disposition") != fallback_disposition:
            return

        # Nothing to read an outcome from. A third of abrupt hangups have no
        # user speech at all -- the caller dropped during the greeting -- and
        # the transcript holds only our own words. Asking the model to explain
        # why such a call ended invites it to invent a reason. The manager is
        # unset on a run torn down before `initialize` completed, which is the
        # same situation: no conversation happened.
        if not (
            self._variable_extraction_manager
            and self._variable_extraction_manager.has_user_turns()
        ):
            logger.debug(
                "No user speech in the conversation; keeping the recorded disposition"
            )
            return

        disposition = coerce_disposition(
            (final_extraction or {}).get(CALL_DISPOSITION_VARIABLE)
        )
        if disposition is None:
            return

        logger.debug(
            f"Refining call disposition: {fallback_disposition} -> {disposition}"
        )
        self.record_call_disposition(disposition)
        self.record_call_tags([disposition])

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
        self,
        reason: str,
        abort_immediately: bool = False,
    ):
        """End the pipeline and record its status and business outcome.

        Args:
            reason: Observable call-termination mechanism.
            abort_immediately: Queue a cancellation instead of a graceful end.
        """
        if self._call_disposed:
            logger.debug(f"Call already Disposed: {self._call_disposed}")
            return

        self._call_disposed = True

        # Mute the pipeline
        self._mute_pipeline = True

        # The status/end reason are call mechanics. They are observed here and
        # never generated by an LLM.
        self._gathered_context[CALL_STATUS_CONTEXT_KEY] = reason
        self._gathered_context[END_REASON_CONTEXT_KEY] = reason

        # Prefer a business outcome already recorded during the call -- by an
        # end-call tool or a transfer. Otherwise the mechanical status is the
        # disposition fallback.
        #
        # Stamped before the extraction below rather than after it, so that a
        # call always carries an outcome even when that extraction times out or
        # comes back with nothing. `refine_call_disposition` upgrades it once
        # the extraction has actually landed.
        recorded_disposition = self._gathered_context.get("call_disposition", "")
        disposition_is_fallback = not bool(recorded_disposition)
        call_disposition = (
            recorded_disposition or self._gathered_context[CALL_STATUS_CONTEXT_KEY]
        )
        self._gathered_context["call_disposition"] = call_disposition
        # A dict lookup against the mapping loaded in `initialize`, not a DB
        # round trip -- see `_disposition_mapping`.
        self._gathered_context["mapped_call_disposition"] = self.map_disposition(
            call_disposition
        )

        # Tagged with the untranslated disposition. Tags are Dograh's own
        # vocabulary -- `user_speech`, `not_connected` -- and are what the
        # mapping-less view of a run is read from.
        self.record_call_tags([call_disposition])

        if reason not in (
            EndTaskReason.PIPELINE_ERROR.value,
            EndTaskReason.VOICEMAIL_DETECTED.value,
        ):
            # Flush in-flight + current-node extractions synchronously before
            # ending, bounded so a stuck LLM cannot hold the call open -- see
            # FINAL_EXTRACTION_TIMEOUT_SECONDS.
            final_extraction = None
            try:
                final_extraction = await asyncio.wait_for(
                    self.perform_final_variable_extraction(
                        extract_disposition=disposition_is_fallback
                    ),
                    timeout=FINAL_EXTRACTION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"Final variable extraction did not finish within "
                    f"{FINAL_EXTRACTION_TIMEOUT_SECONDS}s; keeping the recorded "
                    f"disposition '{call_disposition}'"
                )
            # Upgrade the status fallback only from the extraction just
            # awaited. This costs no extra LLM round trip and cannot reuse a
            # stale result from an earlier node.
            if disposition_is_fallback:
                self.refine_call_disposition(call_disposition, final_extraction)

        frame_to_push = (
            CancelFrame(reason=reason) if abort_immediately else EndFrame(reason=reason)
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
            f"Finishing run with reason: {reason}, disposition: "
            f"{self._gathered_context.get('call_disposition', call_disposition)} "
            f"queueing frame {frame_to_push}"
        )
        await self.task.queue_frame(frame_to_push)

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
        if self._bot_is_speaking and self._current_node:
            # If we should not allow interruption, mute the pipeline
            if not self._current_node.allow_interrupt:
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

    def create_generation_started_callback(self):
        """
        This callback is called when a new generation starts.
        This is used to reset the flags that control the flow of the engine.
        """
        return engine_callbacks.create_generation_started_callback(self)

    def create_aggregation_correction_callback(self) -> Callable[[str], str]:
        """Create a callback that corrects corrupted aggregation using reference text."""
        return engine_callbacks.create_aggregation_correction_callback(self)

    def set_context(self, context: LLMContext) -> None:
        """Set the LLM context.

        This allows setting the context after the engine has been created,
        which is useful when the context needs to be created after the engine.
        """
        self.context = context

    def set_task(self, task: PipelineWorker) -> None:
        """Set the pipeline task.

        This allows setting the task after the engine has been created,
        which is useful when the task needs to be created after the engine.
        """
        self.task = task

    def set_audio_config(self, audio_config) -> None:
        """Set the audio configuration for the pipeline."""
        self._audio_config = audio_config

    def set_integration_capabilities(self, capabilities) -> None:
        """Set the call capabilities contributed by enabled integrations.

        Set after construction, like the context: the integration runtime
        context is built alongside the pipeline components.
        """
        self._integration_capabilities = list(capabilities or [])

    def _resolve_integration_addenda(self) -> list[str]:
        """Render each integration's prompt addendum for the current context."""
        addenda: list[str] = []
        for capability in self._integration_capabilities:
            if capability.prompt_addendum is None:
                continue
            try:
                text = capability.prompt_addendum(self._call_context_vars)
            except Exception as e:
                logger.warning(
                    f"Integration {capability.name!r} prompt addendum failed, "
                    f"skipping: {e}"
                )
                continue
            if text and text.strip():
                addenda.append(text.strip())
        return addenda

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

    async def handle_llm_text_frame(self, text: str):
        """Accumulate LLM text frames to build reference text."""
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

    async def _open_mcp_sessions(self) -> None:
        """Connect every MCP-category tool referenced by any workflow node.
        Failures degrade (session marked unavailable); never raises."""
        from api.services.workflow.tools.mcp_tool import (
            McpDefinitionError,
            validate_mcp_definition,
        )

        try:
            tool_uuids: set[str] = set()
            for node in self.workflow.nodes.values():
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
                await session.start()
                self._mcp_sessions[tool.tool_uuid] = session
        except Exception as e:
            logger.warning(
                f"Failed to open MCP sessions; call proceeds without MCP tools: {e}",
                exc_info=True,
            )

    async def close_mcp_sessions(self) -> None:
        """Close all open MCP tool sessions.

        Must run in the same task that ran initialize() (which opened the
        sessions via _open_mcp_sessions). The MCP client's underlying anyio
        cancel scopes are task-affine — they must be exited from the task that
        entered them — so this is invoked from _run_pipeline's finally, not
        from cleanup() (which runs in a pipecat event-handler task).
        """
        for tool_uuid, session in list(self._mcp_sessions.items()):
            try:
                await session.close()
            except Exception as e:
                logger.warning(f"Error closing MCP session {tool_uuid}: {e}")
        self._mcp_sessions = {}

    async def cleanup(self):
        """Clean up engine resources on disconnect.

        MCP tool sessions are intentionally NOT closed here — see
        close_mcp_sessions(). This method runs in a pipecat event-handler task
        (on_pipeline_finished), a different task than the one that opened the
        MCP sessions; closing them here raises "Attempted to exit cancel scope
        in a different task than it was entered in".
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
