from __future__ import annotations

from typing import TYPE_CHECKING, Any, List

from loguru import logger
from opentelemetry import trace

from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.tracing_config import is_tracing_enabled
from api.services.workflow.dto import ExtractionVariableDTO
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

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
        # Build a normalized representation of the existing conversation so the
        # extractor works with both OpenAI-style (dict) messages and Google
        # Gemini `Content` objects.
        # ------------------------------------------------------------------
        def _get_role_and_content(msg: Any) -> tuple[str | None, str | None]:
            """Return a pair of (role, content) for the given message.

            The logic supports both OpenAI-style dict messages and Google
            `Content` objects that expose ``role`` and ``parts`` attributes.
            Only plain textual content is extracted – image parts, tool call
            placeholders, etc. are ignored for the purpose of variable
            extraction.
            """

            # --------------------------------------------------------------
            # OpenAI format → simple dict with ``role`` and ``content`` keys
            # --------------------------------------------------------------
            if isinstance(msg, dict):
                role = msg.get("role")
                content_field = msg.get("content")

                # Content can be a str, list of segments, or None.
                if isinstance(content_field, str):
                    content = content_field
                elif isinstance(content_field, list):
                    # Collapse all text parts into a single string.
                    texts = [
                        segment.get("text", "")
                        for segment in content_field
                        if isinstance(segment, dict) and segment.get("type") == "text"
                    ]
                    content = " ".join(texts) if texts else None
                else:
                    content = None

                return role, content

            # --------------------------------------------------------------
            # Google Gemini format → ``Content`` object with ``parts`` list
            # --------------------------------------------------------------
            role_attr = getattr(msg, "role", None)
            parts_attr = getattr(msg, "parts", None)

            if role_attr is None or parts_attr is None:
                return None, None  # Unrecognised message format

            role = (
                "assistant" if role_attr == "model" else role_attr
            )  # Normalise role name

            # Collect textual parts only (ignore images, function calls, etc.)
            texts: list[str] = []
            for part in parts_attr:
                text_val = getattr(part, "text", None)
                if text_val:
                    texts.append(text_val)

            content = " ".join(texts) if texts else None
            return role, content

        conversation_lines: list[str] = []
        for msg in self._context.messages:
            role, content = _get_role_and_content(msg)
            if role in ("assistant", "user") and content:
                conversation_lines.append(f"{role}: {content}")

        conversation_history = "\n".join(conversation_lines)

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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        extraction_context.set_messages(extraction_messages)

        # ------------------------------------------------------------------
        # Use engine's LLM for out-of-band inference (no pipeline frames)
        # ------------------------------------------------------------------
        llm_response = await self._engine.llm.run_inference(extraction_context)

        # Get model name for tracing
        model_name = getattr(self._engine.llm, "model_name", "unknown")

        if is_tracing_enabled():
            tracer = trace.get_tracer("pipecat")
            with tracer.start_as_current_span(
                "llm-variable-extraction", context=parent_ctx
            ) as span:
                add_llm_span_attributes(
                    span,
                    service_name=self._engine.llm.__class__.__name__,
                    model=model_name,
                    operation_name="llm-variable-extraction",
                    messages=extraction_messages,
                    output=llm_response,
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
