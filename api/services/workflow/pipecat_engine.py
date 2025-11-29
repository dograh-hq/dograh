from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from api.constants import DEPLOYMENT_MODE, ENABLE_TRACING, VOICEMAIL_RECORDING_DURATION
from api.services.workflow.disposition_mapper import (
    apply_disposition_mapping,
    get_organization_id_from_workflow_run,
)
from api.services.workflow.pipecat_engine_voicemail_detector import (
    VoicemailDetector,
)
from api.services.workflow.workflow import Node, WorkflowGraph
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    FunctionCallResultProperties,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.utils.enums import EndTaskReason

if TYPE_CHECKING:
    from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
    from pipecat.processors.audio.audio_buffer_processor import AudioBuffer
    from pipecat.services.anthropic.llm import AnthropicLLMService
    from pipecat.services.google.llm import GoogleLLMService
    from pipecat.services.openai.llm import OpenAILLMService

    LLMService = Union[OpenAILLMService, AnthropicLLMService, GoogleLLMService]

import asyncio

from loguru import logger

from api.services.workflow import pipecat_engine_callbacks as engine_callbacks
from api.services.workflow.pipecat_engine_utils import (
    get_function_schema,
    render_template,
    update_llm_context,
)
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.tools.calculator import get_calculator_tools, safe_calculator
from api.services.workflow.tools.timezone import (
    convert_time,
    get_current_time,
    get_time_tools,
)
from pipecat.processors.filters.stt_mute_filter import STTMuteFilter
from pipecat.utils.tracing.context_registry import get_current_turn_context


class PipecatEngine:
    def __init__(
        self,
        *,
        task: Optional[PipelineTask] = None,
        llm: Optional["LLMService"] = None,
        context: Optional[LLMContext] = None,
        tts: Optional[Any] = None,
        transport: Optional[BaseTransport] = None,
        workflow: WorkflowGraph,
        call_context_vars: dict,
        audio_buffer: Optional["AudioBuffer"] = None,
        workflow_run_id: Optional[int] = None,
    ):
        self.task = task
        self.llm = llm
        self.context = context
        self.tts = tts
        self.transport = transport
        self.workflow = workflow
        self._call_context_vars = call_context_vars
        self._audio_buffer = audio_buffer
        self._workflow_run_id = workflow_run_id
        self._initialized = False
        self._client_disconnected = False
        self._current_node: Optional[Node] = None
        self._gathered_context: dict = {}
        self._user_response_timeout_task: Optional[asyncio.Task] = None
        self._call_disposition: Optional[str] = None

        # Stasis connection for immediate transfers
        self._stasis_connection: Optional["StasisRTPConnection"] = None

        # Will be set later in initialize() when we have
        # access to _context
        self._variable_extraction_manager = None

        # Voicemail detection state
        self._detect_voicemail = False
        self._voicemail_detector = None
        self._voicemail_detection_task: Optional[asyncio.Task] = None

        # Lazy loaded built-in function schemas
        self._builtin_function_schemas: Optional[list[dict]] = None

        # Track current LLM reference text for TTS aggregation correction
        self._current_llm_reference_text: str = ""

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
            try:

                async def on_context_updated() -> None:
                    """
                    pipecat framework will run this function after the function call result has been updated in the context.
                    This way, when we do set_node from within this function, and go for LLM completion with updated
                    system prompts, the context is updated with function call result.
                    """
                    # Perform variable extraction before transitioning to new node
                    await self._perform_variable_extraction_if_needed(
                        self._current_node
                    )
                    await self.set_node(transition_to_node)

                result = {"status": "done"}

                properties = FunctionCallResultProperties(
                    run_llm=False,
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
            cancel_on_interruption=True,
        )

    async def _register_builtin_functions(self):
        """Register built-in functions (calculator and timezone) with the LLM."""
        logger.debug("Registering built-in functions with LLM")

        properties = FunctionCallResultProperties(run_llm=True)

        # Register calculator function
        async def calculate_func(function_call_params: FunctionCallParams) -> None:
            try:
                expr = function_call_params.arguments.get("expression", "")
                result = safe_calculator(expr)
                await function_call_params.result_callback(
                    {"expression": expr, "result": result}, properties=properties
                )
            except Exception as e:
                await function_call_params.result_callback(
                    {"error": str(e)}, properties=properties
                )

        # Register timezone functions
        async def get_current_time_func(
            function_call_params: FunctionCallParams,
        ) -> None:
            try:
                timezone = function_call_params.arguments.get("timezone", "UTC")
                result = get_current_time(timezone)
                await function_call_params.result_callback(
                    result, properties=properties
                )
            except Exception as e:
                await function_call_params.result_callback(
                    {"error": str(e)}, properties=properties
                )

        async def convert_time_func(function_call_params: FunctionCallParams) -> None:
            try:
                result = convert_time(
                    function_call_params.arguments.get("source_timezone"),
                    function_call_params.arguments.get("time"),
                    function_call_params.arguments.get("target_timezone"),
                )
                await function_call_params.result_callback(
                    result, properties=properties
                )
            except Exception as e:
                await function_call_params.result_callback(
                    {"error": str(e)}, properties=properties
                )

        # Register all built-in functions
        self.llm.register_function("safe_calculator", calculate_func)
        self.llm.register_function("get_current_time", get_current_time_func)
        self.llm.register_function("convert_time", convert_time_func)

    async def _queue_tts_response(self, text: str) -> None:
        """Queue TTS frames for static text response."""
        await self.task.queue_frames(
            [
                LLMFullResponseStartFrame(),
                TTSSpeakFrame(text=text),
                LLMFullResponseEndFrame(),
            ]
        )

    async def _perform_variable_extraction_if_needed(
        self, previous_node: Optional[Node]
    ) -> None:
        """Perform variable extraction if the previous node had extraction enabled."""
        if (
            previous_node
            and previous_node.extraction_enabled
            and previous_node.extraction_variables
        ):
            logger.debug(
                f"Scheduling background variable extraction for node: {previous_node.name}"
            )

            # Capture the current turn context before creating the background task
            parent_context = get_current_turn_context()
            extraction_prompt = self._format_prompt(previous_node.extraction_prompt)
            extraction_variables = previous_node.extraction_variables

            async def _background_extraction():
                try:
                    extracted_data = (
                        await self._variable_extraction_manager._perform_extraction(
                            extraction_variables, parent_context, extraction_prompt
                        )
                    )
                    self._gathered_context.update(extracted_data)
                    logger.debug(
                        f"Background variable extraction completed. Extracted: {extracted_data}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error during background variable extraction: {str(e)}"
                    )

            # Fire and forget - extraction happens in background without blocking
            asyncio.create_task(_background_extraction())

    async def _setup_llm_context_and_start_generation(self, node: Node) -> None:
        """Common method to set up LLM context and queue context frame for non-static nodes."""
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

        # Set up system message and functions
        (
            system_message,
            functions,
        ) = await self._compose_system_message_functions_for_node(node)
        await self._update_llm_context(system_message, functions)
        await self.task.queue_frame(LLMContextFrame(self.context))

    async def set_node(self, node_id: str):
        """
        Simplified set_node implementation according to v2 PRD.
        """
        node = self.workflow.nodes[node_id]

        logger.debug(
            f"Executing node: name: {node.name} is_static: {node.is_static} allow_interrupt: {node.allow_interrupt} is_end: {node.is_end}"
        )

        # Set current node for all nodes (including static ones) so STT mute filter works
        self._current_node = node

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
        # Handle voicemail detection setup (before any returns)
        # Lets check ENABLE_TRACING to make sure we have prompt access from
        # langfuse
        if node.detect_voicemail and DEPLOYMENT_MODE == "saas" and ENABLE_TRACING:
            if not self._audio_buffer:
                logger.warning(
                    "Voicemail detection enabled but no audio buffer available - skipping detection"
                )
            else:
                logger.debug(
                    "Start node has detect_voicemail enabled - setting up audio-based detector"
                )
                self._detect_voicemail = True

                self._voicemail_detector = VoicemailDetector(
                    detection_duration=VOICEMAIL_RECORDING_DURATION,
                    workflow_run_id=self._workflow_run_id,
                )

                # Register audio handler on the audio buffer input processor
                audio_input = self._audio_buffer.input()

                @audio_input.event_handler("on_input_audio_data")
                async def handle_voicemail_audio(
                    processor, pcm, sample_rate, num_channels
                ):
                    if (
                        self._voicemail_detector
                        and self._voicemail_detector.is_detecting
                    ):
                        await self._voicemail_detector.handle_audio_data(
                            processor, pcm, sample_rate, num_channels
                        )

                # Start detection
                await self._voicemail_detector.start_detection(self)

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
            # Start generation for non-static start node
            await self._setup_llm_context_and_start_generation(node)

    async def _handle_end_node(self, node: Node) -> None:
        """Handle end node execution."""
        if node.is_static:
            raise ValueError("Static nodes are not supported!")
        else:
            await self._setup_llm_context_and_start_generation(node)

        # If this end node has extraction enabled, perform extraction immediately
        if node.extraction_enabled and node.extraction_variables:
            await self._perform_variable_extraction_if_needed(node)

        await self.send_end_task_frame(EndTaskReason.USER_QUALIFIED.value)

    async def _handle_agent_node(self, node: Node) -> None:
        """Handle agent node execution."""
        if node.is_static:
            raise ValueError("Static nodes are not supported!")
        else:
            # Set context and functions for non-static agent node
            await self._setup_llm_context_and_start_generation(node)

    async def send_end_task_frame(
        self,
        reason: str,
        abort_immediately: bool = False,
    ):
        """
        Centralized method to send EndTaskFrame with metadata including
        call_transfer_context and call_context_vars
        """
        frame_to_push = CancelFrame() if abort_immediately else EndFrame()

        # Customer disposition code using their mapping
        mapped_disposition = ""

        # Apply disposition mapping - first try call_disposition if it is,
        # extracted from the call conversation then fall back to reason
        call_disposition = self._gathered_context.get("call_disposition", "")
        organization_id = await get_organization_id_from_workflow_run(
            self._workflow_run_id
        )

        # If client is disconnected before we get a chance to disconnect from
        # the bot, lets consider that as final disposition
        if self._client_disconnected:
            call_disposition = EndTaskReason.USER_HANGUP.value

        if call_disposition:
            # If call_disposition exists, map it
            mapped_disposition = await apply_disposition_mapping(
                call_disposition, organization_id
            )
            # Store the original and mapped values
            self._gathered_context["extracted_call_disposition"] = call_disposition
            self._gathered_context["call_disposition"] = mapped_disposition
        else:
            # Otherwise, map the disconnect reason
            mapped_disposition = await apply_disposition_mapping(
                reason, organization_id
            )
            # Store the mapped disconnect reason
            self._gathered_context["call_disposition"] = mapped_disposition

        # TODO: Generalise this
        self._gathered_context["address"] = ", ".join(
            [
                self._call_context_vars.get("address1", ""),
                self._call_context_vars.get("address2", ""),
                self._call_context_vars.get("address3", ""),
                self._call_context_vars.get("city", ""),
                self._call_context_vars.get("state", ""),
                self._call_context_vars.get("province", ""),
                self._call_context_vars.get("postal_code", ""),
            ]
        )
        self._gathered_context["full_name"] = " ".join(
            [
                self._call_context_vars.get("first_name", ""),
                self._call_context_vars.get("middle_initial", ""),
                self._call_context_vars.get("last_name", ""),
            ]
        )
        self._gathered_context["agent_name"] = "Alex"
        self._gathered_context["customer_phone_number"] = self._call_context_vars.get(
            "phone", ""
        )
        self._gathered_context["timezone"] = self._call_context_vars.get("province", "")
        self._gathered_context["vendor_id"] = self._call_context_vars.get(
            "vendor_lead_code", ""
        )

        decision_maker = self._gathered_context.get("primary_cardholder", False)
        employment_status = self._gathered_context.get("employment_status", "N/A")
        call_transfer_context = {
            "first_name": self._call_context_vars.get("first_name", ""),
            "full_name": self._gathered_context.get("full_name", ""),
            "phone": self._call_context_vars.get("phone", ""),
            "lead_id": self._call_context_vars.get("lead_id"),
            "disposition": mapped_disposition,
            "agent_name": self._gathered_context.get("agent_name", "Alex"),
            "decision_maker": str(decision_maker),
            "employment": employment_status.title() if employment_status else "N/A",
            "debts": self._gathered_context.get("total_debt", "N/A"),
            "number_of_credit_cards": self._gathered_context.get(
                "number_of_credit_cards", "N/A"
            ),
            "time": self._gathered_context.get("time"),
        }

        logger.debug(
            f"gathered_context: {self._gathered_context} call_transfer_context: {call_transfer_context}"
        )

        # Initiate immediate transfer for Stasis connections when user is qualified
        if (
            reason == EndTaskReason.USER_QUALIFIED.value
            and self._stasis_connection is not None
            and not abort_immediately
        ):
            try:
                logger.info(
                    f"Initiating immediate Stasis transfer for channel {self._stasis_connection.channel_id}"
                )
                await self._stasis_connection.transfer(call_transfer_context)
                logger.info("Immediate transfer initiated successfully")
            except Exception as e:
                logger.error(f"Failed to initiate immediate transfer: {e}")
                # Continue with normal flow even if immediate transfer fails

        if reason == EndTaskReason.CALL_DURATION_EXCEEDED.value:
            await self.task.queue_frame(
                TTSSpeakFrame(
                    "Sorry! It seems like our time has exceeded. Someone from our team will reach out to you soon. Thank you!"
                )
            )

        # Store the original reason for later retrieval in event handler
        self._call_disposition = mapped_disposition

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

    def create_should_mute_callback(self) -> Callable[[STTMuteFilter], Awaitable[bool]]:
        """
        This callback is called by STTMuteFilter to determine if the STT should be muted.
        """
        return engine_callbacks.create_should_mute_callback(self)

    def create_user_idle_callback(self):
        """
        This callback is called when the user is idle for a certain duration.
        We use this to either play the static text or end the call
        """
        return engine_callbacks.create_user_idle_callback(self)

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

    def set_audio_buffer(self, audio_buffer: "AudioBuffer") -> None:
        """Set the audio buffer.

        This allows setting the audio buffer after the engine has been created,
        which is useful when the audio buffer needs to be created after the engine.
        """
        self._audio_buffer = audio_buffer

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

    async def handle_llm_text_frame(self, text: str):
        """Accumulate LLM text frames to build reference text."""
        self._current_llm_reference_text += text

    async def handle_client_disconnected(self):
        """Handle client disconnected event."""
        self._client_disconnected = True

    async def get_call_disposition(self) -> Optional[str]:
        """Get the disconnect reason set by the engine."""
        if self._call_disposition:
            # We would have a _call_disposition variable set if we have initiated
            # a disconnect from the bot, i.e we have called send_end_task_frame.
            return self._call_disposition

        if self._client_disconnected:
            return EndTaskReason.USER_HANGUP.value
        else:
            return EndTaskReason.UNKNOWN.value

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

        # Stop voicemail detection if active
        if self._voicemail_detector and hasattr(
            self._voicemail_detector, "stop_detection"
        ):
            await self._voicemail_detector.stop_detection()
