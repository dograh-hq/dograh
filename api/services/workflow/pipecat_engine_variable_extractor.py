from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, List

from loguru import logger
from opentelemetry import trace
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.tracing_config import ensure_tracing
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

    # Keys stripped from HTTP tool responses before passing to the extraction
    _TOOL_RESPONSE_STRIP_KEYS = {"status", "status_code"}

    # Maximum character length for a single tool response in the extraction
    # context.  Responses longer than this are truncated with a marker.
    _TOOL_RESPONSE_MAX_CHARS = 2000

    # Transition tool response
    _TRANSITION_RESPONSE = '{"status": "done"}'

    def _build_tool_call_name_lookup(self) -> dict[str, str]:
        """Build a mapping of tool_call_id → function name from assistant messages.

        This allows labelling tool responses with the function that produced them.
        """
        lookup: dict[str, str] = {}
        for msg in self._context.messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id")
                func = tc.get("function") or {}
                tc_name = func.get("name")
                if tc_id and tc_name:
                    lookup[tc_id] = tc_name
        return lookup

    def _format_tool_response(self, raw_content: str, tool_name: str) -> str | None:
        """Clean, trim, and format a tool response for the extraction context.

        Returns None if the response should be excluded (e.g. transition tools).
        """
        # Skip transition tool responses
        if raw_content.strip() == self._TRANSITION_RESPONSE:
            return None

        # Try to parse as JSON so we can strip wrapper keys and extract data
        try:
            parsed = json.loads(raw_content)
            if isinstance(parsed, dict):
                # If there is a "data" key, prefer its content — that is the
                # actual HTTP response payload from custom tools.
                if "data" in parsed:
                    parsed = parsed["data"]
                else:
                    # Strip wrapper metadata keys
                    for key in self._TOOL_RESPONSE_STRIP_KEYS:
                        parsed.pop(key, None)

                formatted = json.dumps(parsed, ensure_ascii=False)
            else:
                formatted = raw_content
        except (json.JSONDecodeError, TypeError):
            formatted = raw_content

        # Truncate if too long
        if len(formatted) > self._TOOL_RESPONSE_MAX_CHARS:
            formatted = formatted[: self._TOOL_RESPONSE_MAX_CHARS] + "...(truncated)"

        return f"[Tool Response: {tool_name}]\n{formatted}"

    def _get_role_and_content(self, msg: Any) -> tuple[str | None, str | None]:
        """Return (role, content) for a single context message.

        Supports both OpenAI-style dict messages and Google Gemini ``Content``
        objects.  Only plain textual content is returned — image parts, tool
        call placeholders, etc. are ignored.
        """
        # OpenAI format — dict with ``role`` and ``content`` keys
        if isinstance(msg, dict):
            role = msg.get("role")
            content_field = msg.get("content")

            if isinstance(content_field, str):
                return role, content_field
            if isinstance(content_field, list):
                texts = [
                    segment.get("text", "")
                    for segment in content_field
                    if isinstance(segment, dict) and segment.get("type") == "text"
                ]
                return role, (" ".join(texts) if texts else None)
            return role, None

        # Google Gemini format — ``Content`` object with ``parts`` list
        role_attr = getattr(msg, "role", None)
        parts_attr = getattr(msg, "parts", None)
        if role_attr is None or parts_attr is None:
            return None, None

        role = "assistant" if role_attr == "model" else role_attr
        texts = [t for p in parts_attr if (t := getattr(p, "text", None))]
        return role, (" ".join(texts) if texts else None)

    def _build_conversation_history(self) -> str:
        """Build a text representation of the conversation for the extraction LLM.

        Includes assistant/user messages and formatted tool responses (excluding
        transition tool responses).
        """
        tool_call_names = self._build_tool_call_name_lookup()

        lines: list[str] = []
        for msg in self._context.messages:
            role, content = self._get_role_and_content(msg)
            if role in ("assistant", "user") and content:
                lines.append(f"{role}: {content}")
            elif isinstance(msg, dict) and msg.get("role") == "tool":
                tool_content = msg.get("content", "")
                tool_call_id = msg.get("tool_call_id", "")
                tool_name = tool_call_names.get(tool_call_id, "unknown")
                formatted = self._format_tool_response(tool_content, tool_name)
                if formatted:
                    lines.append(formatted)

        return "\n".join(lines)

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
        # Build a normalized conversation history including tool responses.
        # ------------------------------------------------------------------
        conversation_history = self._build_conversation_history()

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
        llm_response = await self._engine.inference_llm.run_inference(
            extraction_context, system_instruction=system_prompt
        )

        # Get model name for tracing
        model_name = getattr(self._engine.inference_llm, "model_name", "unknown")

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
                    service_name=self._engine.inference_llm.__class__.__name__,
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
