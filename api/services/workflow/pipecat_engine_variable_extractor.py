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


ENGINE_OWNED_CONTEXT_KEYS = frozenset(
    {
        "call_disposition",
        "mapped_call_disposition",
        "call_status",
        "call_tags",
        "answer_supervisor",
        "tool_results",
        "llm_fallback_metrics",
        "sip_call_id",
        "extracted_variables",
        "nodes_visited",
        "agent_visits",
        "agent_transfers",
    }
)


class ExtractionResponseError(ValueError):
    """The model did not return a usable extraction response."""


class VariableExtractionManager:
    """Run node extraction against the live engine's conversation."""

    def __init__(self, engine: "PipecatEngine") -> None:
        self._engine = engine
        self._context = engine.context

    async def _perform_extraction(
        self,
        extraction_variables: List[ExtractionVariableDTO],
        parent_ctx: Any,
        extraction_prompt: str = "",
    ) -> dict:
        return await extract_variables(
            self._engine.active_agent.variable_extraction_llm,
            extraction_variables,
            build_conversation_history(self._context),
            extraction_prompt=extraction_prompt,
            parent_ctx=parent_ctx,
        )


async def extract_variables(
    llm: Any,
    extraction_variables: List[ExtractionVariableDTO],
    conversation_history: str,
    *,
    extraction_prompt: str = "",
    parent_ctx: Any = None,
    strict: bool = False,
) -> dict:
    """Extract node variables without owning a pipeline or swallowing failures."""
    vars_description = "\n".join(
        f"- {v.name} ({v.type}): {v.prompt}" for v in extraction_variables
    )

    system_prompt = (
        "You are an assistant tasked with extracting structured data from the conversation. "
        "Return ONLY a valid JSON object with the requested variables as top-level keys, "
        "written on a single line with no line breaks. Do not wrap the JSON in markdown."
    )
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

    # Override any conversational system instruction retained by the client.
    llm_response = await llm.run_inference(
        extraction_context, system_instruction=system_prompt
    )

    model_name = getattr(llm, "model_name", "unknown")

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
                service_name=llm.__class__.__name__,
                model=model_name,
                operation_name="llm-variable-extraction",
                messages=tracing_messages,
                output=json.dumps({"content": llm_response}),
                stream=False,
                parameters={},
            )

    if strict and (not llm_response or not llm_response.strip()):
        raise ExtractionResponseError("Empty extraction response")
    if llm_response is None:
        logger.warning("Extractor returned no response; returning empty result.")
        extracted = {}
    else:
        try:
            extracted = parse_llm_json(llm_response, raise_on_error=strict)
        except ValueError as exc:
            raise ExtractionResponseError("Invalid extraction JSON") from exc
        if strict and (
            not isinstance(extracted, dict)
            or not extracted.keys() & {v.name for v in extraction_variables}
        ):
            raise ExtractionResponseError("Invalid extraction response")
        if not strict and "raw" in extracted and len(extracted) == 1:
            logger.warning(
                "Extractor returned invalid JSON; storing raw content instead."
            )

    logger.debug(f"Extracted variables: {extracted}")
    return extracted
