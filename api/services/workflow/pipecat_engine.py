from typing import TYPE_CHECKING, Awaitable, Callable, Optional, Union

from api.services.workflow.disposition_mapper import (
    apply_disposition_mapping,
    get_organization_id_from_workflow_run,
)
from api.services.workflow.workflow import Node, WorkflowGraph
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    FunctionCallResultProperties,
)
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.utils.enums import EndTaskReason

if TYPE_CHECKING:
    from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
    from pipecat.frames.frames import Frame
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.openai.llm import OpenAILLMService

    LLMService = Union[OpenAILLMService, AnthropicLLMService, GoogleLLMService]

import asyncio

from loguru import logger

from api.services.workflow import pipecat_engine_callbacks as engine_callbacks
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.pipecat_engine_utils import (
    get_function_schema,
    render_template,
    update_llm_context,
)
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.tools.calculator import get_calculator_tools, safe_calculator
from api.services.workflow.tools.knowledge_base import (
    get_knowledge_base_tool,
    retrieve_from_knowledge_base,
)
from api.services.workflow.tools.timezone import (
    convert_time,
    get_current_time,
    get_time_tools,
)
from pipecat.utils.tracing.context_registry import get_current_turn_context


class PipecatEngine:
    def __init__(
        self,
        *,
        task: Optional[PipelineTask] = None,
        llm: Optional["LLMService"] = None,
        context: Optional[LLMContext] = None,
        workflow: WorkflowGraph,
        call_context_vars: dict,
        workflow_run_id: Optional[int] = None,
        node_transition_callback: Optional[
            Callable[[str, Optional[str]], Awaitable[None]]
        ] = None,
        embeddings_api_key: Optional[str] = None,
        embeddings_model: Optional[str] = None,
        embeddings_base_url: Optional[str] = None,
    ):
        self.task = task
        self.llm = llm
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

        # Stasis connection for immediate transfers
        self._stasis_connection: Optional["StasisRTPConnection"] = None

        # Will be set later in initialize() when we have
        # access to _context
        self._variable_extraction_manager = None

        # Lazy loaded built-in function schemas
        self._builtin_function_schemas: Optional[list[dict]] = None

        # Track current LLM reference text for TTS aggregation correction
        self._current_llm_generation_reference_text: str = ""

        # Controls whether user input should be muted
        self._mute_pipeline: bool = False

        # Tracks whether the bot is currently speaking (for allow_interrupt logic)
        self._bot_is_speaking: bool = False

        # Custom tool manager (initialized in initialize())
        self._custom_tool_manager: Optional[CustomToolManager] = None

        # Embeddings configuration (passed from run_pipeline.py)
        self._embeddings_api_key: Optional[str] = embeddings_api_key
        self._embeddings_model: Optional[str] = embeddings_model
        self._embeddings_base_url: Optional[str] = embeddings_base_url

        # Audio configuration (set via set_audio_config from _run_pipeline)
        self._audio_config = None

    async def _get_organization_id(self) -> Optional[int]:
        """Get and cache the organization ID from workflow run."""
        if self._custom_tool_manager:
            return await self._custom_tool_manager.get_organization_id()
        # Fallback for when manager is not yet initialized
        return await get_organization_id_from_workflow_run(self._workflow_run_id)

    @property
    def builtin_function_schemas(self) -> list[dict]:
        """Get built-in function schemas (calculator and timezone tools)."""
        if self._builtin_function_schemas is None:
            self._builtin_function_schemas = []

            # Transform calculator tools to get_function_schema format
            for tool in get_calculator_tools():
                func = tool["function"]
                schema = get_function_schema(
                    func["name"],
                    func["description"],
                    properties=func["parameters"]["properties"],
                    required=func["parameters"]["required"],
                )
                self._builtin_function_schemas.append(schema)

            # Transform timezone tools to get_function_schema format
            for tool in get_time_tools():
                func = tool["function"]
                schema = get_function_schema(
                    func["name"],
                    func["description"],
                    properties=func["parameters"]["properties"],
                    required=func["parameters"]["required"],
                )
                self._builtin_function_schemas.append(schema)

        return self._builtin_function_schemas

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

            # Add current time in EST (America/New_York) to gathered context
            try:
                est_time_result = get_current_time("America/New_York")
                # The get_current_time utility returns a dict with 'datetime' field
                # Store the ISO formatted datetime string under the key 'time'
                self._gathered_context["time"] = est_time_result.get("datetime")
            except Exception as e:
                logger.error(f"Failed to fetch current EST time: {e}")

            # Register built-in functions with the LLM
            await self._register_builtin_functions()

            await self.set_node(self.workflow.start_node_id)

            logger.debug(f"{self.__class__.__name__} initialized")
        except Exception as e:
            logger.error(f"Error initializing {self.__class__.__name__}: {e}")
            raise

    def _get_function_schema(self, function_name: str, description: str):
        """Thin wrapper around utils.get_function_schema for backwards compatibility."""

        return get_function_schema(function_name, description)

    async def _update_llm_context(self, system_message: dict, functions: list[dict]):
        """Delegate context update to the shared workflow.utils implementation."""

        update_llm_context(self.context, system_message, functions)

    def _format_prompt(self, prompt: str) -> str:
        """Delegate prompt formatting to the shared workflow.utils implementation."""

        return render_template(prompt, self._call_context_vars)

    async def _create_transition_func(self, name: str, transition_to_node: str):
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
        self, name: str, transition_to_node: str
    ):
        logger.debug(
            f"Registering function {name} to transition to node {transition_to_node} with LLM"
        )

        # Create transition function
        transition_func = await self._create_transition_func(name, transition_to_node)

        # Register function with LLM
        self.llm.register_function(
            name,
            transition_func,
            cancel_on_interruption=False,
        )

    async def _register_builtin_functions(self):
        """Register built-in functions (calculator and timezone) with the LLM."""
        logger.debug("Registering built-in functions with LLM")

        # Register calculator function
        async def calculate_func(function_call_params: FunctionCallParams) -> None:
            logger.info(f"LLM Function Call EXECUTED: safe_calculator")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                expr = function_call_params.arguments.get("expression", "")
                result = safe_calculator(expr)
                await function_call_params.result_callback(
                    {"expression": expr, "result": result}
                )
            except Exception as e:
                await function_call_params.result_callback({"error": str(e)})

        # Register timezone functions
        async def get_current_time_func(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"LLM Function Call EXECUTED: get_current_time")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                timezone = function_call_params.arguments.get("timezone", "UTC")
                result = get_current_time(timezone)
                await function_call_params.result_callback(result)
            except Exception as e:
                await function_call_params.result_callback({"error": str(e)})

        async def convert_time_func(function_call_params: FunctionCallParams) -> None:
            logger.info(f"LLM Function Call EXECUTED: convert_time")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                result = convert_time(
                    function_call_params.arguments.get("source_timezone"),
                    function_call_params.arguments.get("time"),
                    function_call_params.arguments.get("target_timezone"),
                )
                await function_call_params.result_callback(result)
            except Exception as e:
                await function_call_params.result_callback({"error": str(e)})

        # Register all built-in functions
        self.llm.register_function("safe_calculator", calculate_func)
        self.llm.register_function("get_current_time", get_current_time_func)
        self.llm.register_function("convert_time", convert_time_func)

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

                if not self._embeddings_api_key:
                    raise ValueError(
                        "Embeddings API key not configured. Please set your API key in "
                        "Model Configurations > Embedding."
                    )

                result = await retrieve_from_knowledge_base(
                    query=query,
                    organization_id=organization_id,
                    document_uuids=document_uuids,
                    limit=3,  # Return top 3 most relevant chunks
                    embeddings_api_key=self._embeddings_api_key,
                    embeddings_model=self._embeddings_model,
                    embeddings_base_url=self._embeddings_base_url,
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
        # before creating the background task
        parent_context = get_current_turn_context()

        extraction_prompt = self._format_prompt(node.extraction_prompt)
        extraction_variables = node.extraction_variables

        async def _do_extraction():
            try:
                extracted_data = (
                    await self._variable_extraction_manager._perform_extraction(
                        extraction_variables, parent_context, extraction_prompt
                    )
                )
                self._gathered_context.update(extracted_data)
                logger.debug(
                    f"Variable extraction completed. Extracted: {extracted_data}"
                )
            except Exception as e:
                logger.error(f"Error during variable extraction: {str(e)}")

        if run_in_background:
            logger.debug(
                f"Scheduling background variable extraction for node: {node.name}"
            )
            asyncio.create_task(_do_extraction())
        else:
            logger.debug(
                f"Performing synchronous variable extraction for node: {node.name}"
            )
            await _do_extraction()

    async def _setup_llm_context(self, node: Node) -> None:
        """Common method to set up LLM context"""
        # Set node name for tracing
        try:
            self.context.set_node_name(node.name)
        except AttributeError:
            logger.warning(f"context has no set_node_name method")

        # Register transition functions if not an end node
        if not node.is_end:
            for outgoing_edge in node.out_edges:
                await self._register_transition_function_with_llm(
                    outgoing_edge.get_function_name(), outgoing_edge.target
                )

        # Register custom tool handlers for this node
        if node.tool_uuids and self._custom_tool_manager:
            await self._custom_tool_manager.register_handlers(node.tool_uuids)

        # Register knowledge base retrieval handler if node has documents
        if node.document_uuids:
            await self._register_knowledge_base_function(node.document_uuids)

        # Set up system message and functions
        (
            system_message,
            functions,
        ) = await self._compose_system_message_functions_for_node(node)
        await self._update_llm_context(system_message, functions)

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

        # Set current node for all nodes (including static ones) so STT mute filter works
        self._current_node = node

        # Send node transition event if callback is provided
        if self._node_transition_callback:
            try:
                await self._node_transition_callback(node.name, previous_node_name)
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

        logger.debug(
            f"Finishing run with reason: {reason}, disposition: {mapped_disposition} queueing frame {frame_to_push}"
        )
        await self.task.queue_frame(frame_to_push)

    async def _compose_system_message_functions_for_node(
        self, node: "Node"
    ) -> tuple[list[dict], list[dict]]:
        """Generate the system messages and function schemas for the given node.

        This performs the same formatting logic used when entering a node but
        does **not** register the functions with the LLM; callers are
        responsible for that.
        """

        global_prompt = ""
        if self.workflow.global_node_id and node.add_global_prompt:
            global_node = self.workflow.nodes[self.workflow.global_node_id]
            global_prompt = self._format_prompt(global_node.prompt)

        functions: list[dict] = []

        # Add built-in function schemas (calculator and timezone tools)
        functions.extend(self.builtin_function_schemas)

        # Add knowledge base retrieval tool if node has documents
        if node.document_uuids:
            kb_tool_def = get_knowledge_base_tool(node.document_uuids)
            kb_schema = get_function_schema(
                kb_tool_def["function"]["name"],
                kb_tool_def["function"]["description"],
                properties=kb_tool_def["function"]["parameters"].get("properties", {}),
                required=kb_tool_def["function"]["parameters"].get("required", []),
            )
            functions.append(kb_schema)

        # Add custom tools from node.tool_uuids
        if node.tool_uuids and self._custom_tool_manager:
            custom_tool_schemas = await self._custom_tool_manager.get_tool_schemas(
                node.tool_uuids
            )
            functions.extend(custom_tool_schemas)

        # Transition functions (schema only; registration handled elsewhere)
        for outgoing_edge in node.out_edges:
            function_schema = self._get_function_schema(
                outgoing_edge.get_function_name(), outgoing_edge.condition
            )
            functions.append(function_schema)

        formatted_node_prompt = self._format_prompt(node.prompt)

        system_message = {
            "role": "system",
            "content": "\n\n".join(
                p for p in (global_prompt, formatted_node_prompt) if p
            ),
        }

        return system_message, functions

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
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False

        # Always mute if pipeline is shutting down
        if self._mute_pipeline:
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

    def set_stasis_connection(
        self, connection: Optional["StasisRTPConnection"]
    ) -> None:
        """Set the Stasis RTP connection for immediate transfers.

        This allows the engine to initiate transfers immediately when XFER
        disposition is detected, without waiting for pipeline shutdown.

        Args:
            connection: The StasisRTPConnection instance, or None for non-Stasis transports
        """
        self._stasis_connection = connection
        if connection:
            logger.debug(
                f"Stasis connection set for immediate transfers: {connection.channel_id}"
            )

    def set_audio_config(self, audio_config) -> None:
        """Set the audio configuration for the pipeline."""
        self._audio_config = audio_config

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
