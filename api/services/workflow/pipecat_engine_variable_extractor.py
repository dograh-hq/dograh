from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, List

from loguru import logger
from opentelemetry import trace
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.tracing_config import ensure_tracing
from api.services.workflow.conversation_history import build_conversation_history
from api.services.workflow.dto import ExtractionVariableDTO

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


class VariableExtractionManager:
    """Helper that registers and executes the \"extract_variables\" tool.

    The manager is responsible for two things:
      1. Registering a callable with the LLM service so that the tool can be
         invoked from within the model.
      2. Executing the extraction in a background task while maintaining
         correct bookkeeping and optional OpenTelemetry tracing.
    """

    def __init__(self, engine: "PipecatEngine") -> None:  # noqa: F821
        # We keep a reference to the engine so we can reuse its context
        # and update internal counters / extracted variable state.
        self._engine = engine
        self._context = engine.context

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _perform_extraction(
        self,
        extraction_variables: List[ExtractionVariableDTO],
        parent_ctx: Any,
        extraction_prompt: str = "",
    ) -> dict:
        """Run the actual extraction chat completion and post-process the result."""

        # ------------------------------------------------------------------
        # Build the prompt that instructs the model to extract the variables.
        # ------------------------------------------------------------------
        vars_description = "\n".join(
            f"- {v.name} ({v.type}): {v.prompt}" for v in extraction_variables
        )

        # ------------------------------------------------------------------
        # Build a normalized conversation history including tool calls and responses.
        # ------------------------------------------------------------------
        conversation_history = build_conversation_history(self._context)

        system_prompt = (
            "You are an assistant tasked with extracting structured data from the conversation. "
            "Return ONLY a valid JSON object with the requested variables as top-level keys. Do not wrap the JSON in markdown."  # noqa: E501
        )
        # Use provided extraction_prompt as system prompt, or default
        system_prompt = (
            system_prompt + "\n\n" + extraction_prompt
            if extraction_prompt
            else system_prompt
        )

        user_prompt = (
            "\n\nVariables to extract:\n"
            f"{vars_description}"
            "\n\nConversation history:\n"
            f"{conversation_history}"
        )

        extraction_context = LLMContext()
        extraction_messages = [
            {"role": "user", "content": user_prompt},
        ]
        extraction_context.set_messages(extraction_messages)

        # ------------------------------------------------------------------
        # Use engine's LLM for out-of-band inference (no pipeline frames).
        # Pass system_prompt via system_instruction so it overrides the
        # current node's system prompt that build_chat_completion_params
        # would otherwise prepend.
        # ------------------------------------------------------------------
        llm_response = await self._engine.variable_extraction_llm.run_inference(
            extraction_context, system_instruction=system_prompt
        )

        # Get model name for tracing
        model_name = getattr(
            self._engine.variable_extraction_llm, "model_name", "unknown"
        )

        if ensure_tracing():
            tracer = trace.get_tracer("pipecat")
            with tracer.start_as_current_span(
                "llm-variable-extraction", context=parent_ctx
            ) as span:
                tracing_messages = [
                    {"role": "system", "content": system_prompt},
                    *extraction_messages,
                ]
                add_llm_span_attributes(
                    span,
                    service_name=self._engine.variable_extraction_llm.__class__.__name__,
                    model=model_name,
                    operation_name="llm-variable-extraction",
                    messages=tracing_messages,
                    output=json.dumps({"content": llm_response}),
                    stream=False,
                    parameters={},
                )

        # ------------------------------------------------------------------
        # Parse the assistant output – fall back to raw text if it is not valid JSON.
        # Uses parse_llm_json which handles common LLM mistakes like markdown
        # code blocks (```json ... ```) and extra text around the JSON.
        # ------------------------------------------------------------------
        if llm_response is None:
            logger.warning("Extractor returned no response; returning empty result.")
            extracted = {}
        else:
            extracted = parse_llm_json(llm_response)
            if "raw" in extracted and len(extracted) == 1:
                logger.warning(
                    "Extractor returned invalid JSON; storing raw content instead."
                )

        logger.debug(f"Extracted variables: {extracted}")
        return extracted
