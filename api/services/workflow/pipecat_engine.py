from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Union

from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallResultProperties,
    TTSSpeakFrame,
)
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.settings import LLMSettings
from pipecat.utils.enums import EndTaskReason

from api.db import db_client
from api.services.pipecat.audio_playback import play_audio
from api.services.workflow.disposition_mapper import apply_disposition_mapping
from api.services.workflow.workflow import Node, WorkflowGraph

if TYPE_CHECKING:
    from pipecat.frames.frames import Frame
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.utils.tracing.tracing_context import TracingContext

    LLMService = Union[OpenAILLMService, AnthropicLLMService, GoogleLLMService]

import asyncio

from loguru import logger

from api.services.workflow import pipecat_engine_callbacks as engine_callbacks
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


class PipecatEngine:
    def __init__(
        self,
        *,
        task: Optional[PipelineTask] = None,
        llm: Optional["LLMService"] = None,
        inference_llm: Optional["LLMService"] = None,
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
        has_recordings: bool = False,
        context_compaction_enabled: bool = False,
    ):
        self.task = task
        self.llm = llm
        # LLM used for out-of-band inference (variable extraction, context
        # summarization). Falls back to the pipeline LLM when not provided.
        # In realtime mode the pipeline LLM is a speech-to-speech service
        # that does not implement run_inference, so a separate text LLM
        # must be passed in.
        self.inference_llm = inference_llm or llm
        self.context = context
        self.workflow = workflow
        self._call_context_vars = call_context_vars
        self._workflow_run_id = workflow_run_id
        self._node_transition_callback = node_transition_callback
        self._initialized = False
        self._call_disposed = False
        self._current_node: Optional[Node] = None
        self._gathered_context: dict = {}
        self._user_response_timeout_task: Optional[asyncio.Task] = None
        self._pending_extraction_tasks: set[asyncio.Task] = set()

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

        # Custom tool manager (initialized in initialize())
        self._custom_tool_manager: Optional[CustomToolManager] = None

        # Cached organization ID (resolved lazily from workflow run)
        self._organization_id: Optional[int] = None

        # Embeddings configuration (passed from run_pipeline.py)
        self._embeddings_api_key: Optional[str] = embeddings_api_key
        self._embeddings_model: Optional[str] = embeddings_model
        self._embeddings_base_url: Optional[str] = embeddings_base_url

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
                await self._perform_variable_extraction_if_needed(self._current_node)

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
                        await self.end_call_with_reason(
                            EndTaskReason.USER_QUALIFIED.value
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
        self.llm.register_function(name, transition_func)

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
        self, node: Optional[Node], run_in_background: bool = True
    ) -> None:
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
        extraction_variables = [
            v.model_copy(update={"prompt": self._format_prompt(v.prompt)})
            if v.prompt
            else v
            for v in node.extraction_variables
        ]

        async def _do_extraction():
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
                    return
                self._gathered_context.update(extracted_data)
                extracted_variables = self._gathered_context.setdefault(
                    "extracted_variables", {}
                )
                extracted_variables.update(extracted_data)
                logger.debug(
                    f"Variable extraction completed for node: {node.name}. Extracted: {extracted_data}"
                )
            except Exception as e:
                logger.error(
                    f"Error during variable extraction for node {node.name}: {str(e)}"
                )

        if run_in_background:
            logger.debug(
                f"Scheduling background variable extraction for node: {node.name}"
            )
            task = asyncio.create_task(
                _do_extraction(), name=f"variable-extraction:{node.name}"
            )
            self._pending_extraction_tasks.add(task)
            task.add_done_callback(self._pending_extraction_tasks.discard)
        else:
            logger.debug(
                f"Performing synchronous variable extraction for node: {node.name}"
            )
            await _do_extraction()

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
            await self._custom_tool_manager.register_handlers(node.tool_uuids)

        # Register knowledge base retrieval handler if node has documents
        if node.document_uuids:
            await self._register_knowledge_base_function(node.document_uuids)

        # Compose prompt and functions via the context composer module
        system_prompt = compose_system_prompt_for_node(
            node=node,
            workflow=self.workflow,
            format_prompt=self._format_prompt,
            has_recordings=self._has_recordings,
        )
        functions = await compose_functions_for_node(
            node=node,
            custom_tool_manager=self._custom_tool_manager,
        )
        await self._update_llm_context(system_prompt, functions)

    async def set_node(self, node_id: str):
        """
        Simplified set_node implementation according to v2 PRD.
        """
        node = self.workflow.nodes[node_id]

        logger.debug(
            f"Executing node: name: {node.name} is_static: {node.is_static} allow_interrupt: {node.allow_interrupt} is_end: {node.is_end}"
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
        if self._node_transition_callback:
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

        if node.is_static:
            raise ValueError("Static nodes are not supported!")
        else:
            # Setup LLM Context with Prompts and Functions
            await self._setup_llm_context(node)

    def get_start_greeting(self) -> Optional[tuple[str, Optional[str]]]:
        """Return the greeting info for the start node, or None if not configured.

        Returns:
            A tuple of (greeting_type, value) where:
            - ("text", rendered_text) for text greetings spoken via TTS
            - ("audio", recording_id) for pre-recorded audio greetings
            Or None if no greeting is configured.
        """
        start_node = self.workflow.nodes.get(self.workflow.start_node_id)
        if not start_node:
            return None

        greeting_type = start_node.greeting_type or "text"

        if greeting_type == "audio" and start_node.greeting_recording_id:
            return ("audio", start_node.greeting_recording_id)

        if start_node.greeting:
            return ("text", self._format_prompt(start_node.greeting))

        return None

    async def _handle_end_node(self, node: Node) -> None:
        """Handle end node execution."""
        if node.is_static:
            raise ValueError("Static nodes are not supported!")
        else:
            # Setup LLM Context with Prompts and Functions
            await self._setup_llm_context(node)

    async def _handle_agent_node(self, node: Node) -> None:
        """Handle agent node execution."""
        if node.is_static:
            raise ValueError("Static nodes are not supported!")
        else:
            # Setup LLM Context with Prompts and Functions
            await self._setup_llm_context(node)

    async def end_call_with_reason(
        self,
        reason: str,
        abort_immediately: bool = False,
    ):
        """
        Centralized method to end the call with disposition mapping
        """
        if self._call_disposed:
            logger.debug(f"Call already Disposed: {self._call_disposed}")
            return

        self._call_disposed = True

        # Mute the pipeline
        self._mute_pipeline = True

        if reason not in (
            EndTaskReason.PIPELINE_ERROR.value,
            EndTaskReason.VOICEMAIL_DETECTED.value,
        ):
            # Await any in-flight background extractions from previous nodes
            await self._await_pending_extractions()

            # Perform final variable extraction synchronously before ending
            await self._perform_variable_extraction_if_needed(
                self._current_node, run_in_background=False
            )

        frame_to_push = (
            CancelFrame(reason=reason) if abort_immediately else EndFrame(reason=reason)
        )

        # Apply disposition mapping - first try call_disposition if it is,
        # extracted from the call conversation then fall back to reason
        call_disposition = self._gathered_context.get("call_disposition", "")
        organization_id = await self._get_organization_id()

        if call_disposition:
            # If call_disposition exists, map it
            mapped_disposition = await apply_disposition_mapping(
                call_disposition, organization_id
            )
            # Store the original and mapped values
            self._gathered_context["extracted_call_disposition"] = call_disposition
            self._gathered_context["call_disposition"] = call_disposition
            self._gathered_context["mapped_call_disposition"] = mapped_disposition
        else:
            # Otherwise, map the disconnect reason
            mapped_disposition = await apply_disposition_mapping(
                reason, organization_id
            )
            # Store the mapped disconnect reason
            self._gathered_context["call_disposition"] = reason
            self._gathered_context["mapped_call_disposition"] = mapped_disposition

        effective_disposition = self._gathered_context.get("call_disposition", "")
        if effective_disposition:
            call_tags = self._gathered_context.get("call_tags", [])
            if effective_disposition not in call_tags:
                call_tags.append(effective_disposition)
            self._gathered_context["call_tags"] = call_tags

        logger.debug(
            f"Finishing run with reason: {reason}, disposition: {mapped_disposition} queueing frame {frame_to_push}"
        )
        await self.task.queue_frame(frame_to_push)

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
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            self._queued_speech_mute_state = "idle"

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

    def set_task(self, task: PipelineTask) -> None:
        """Set the pipeline task.

        This allows setting the task after the engine has been created,
        which is useful when the task needs to be created after the engine.
        """
        self.task = task

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

    async def handle_llm_text_frame(self, text: str):
        """Accumulate LLM text frames to build reference text."""
        self._current_llm_generation_reference_text += text

    def is_call_disposed(self):
        """Check whether a call has been disposed by the engine"""
        return self._call_disposed

    async def get_gathered_context(self) -> dict:
        """Get the gathered context including extracted variables."""
        return self._gathered_context.copy()

    async def cleanup(self):
        """Clean up engine resources on disconnect."""
        # Cancel any pending timeout tasks
        if (
            self._user_response_timeout_task
            and not self._user_response_timeout_task.done()
        ):
            self._user_response_timeout_task.cancel()

        # Cancel any in-flight background summarization
        if self._context_summarization_manager:
            await self._context_summarization_manager.cleanup()
