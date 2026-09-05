"""Derive a call's business outcome from the conversation at teardown.

``gathered_context`` carries two different kinds of answer about a finished
call, and conflating them is what made every end-node call read as qualified:

``call_status``
    The *mechanism* -- how the call terminated. ``user_hangup``,
    ``end_call``, ``voicemail_detected``, ``no_answer``. Always known, never
    inferred.

``call_disposition``
    The *outcome* -- what happened commercially. ``not_interested``,
    ``wrong_number``. This is what a dialer reads as a lead status, and it
    drives one decision: call this person again or not.

The engine records the status and a conservative disposition before teardown,
so a run whose pipeline is killed still records something. When a workflow has
configured call dispositions, the engine makes one dedicated final
extraction request to refine that fallback. It is deliberately separate from
node variable extraction: dispositions describe the whole call, not the node
that happened to be active when it ended.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from loguru import logger
from opentelemetry import trace
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

from api.errors.failure import (
    classify_exception,
    failure_metadata_for_processor,
    log_failure,
)
from api.schemas.workflow_configurations import (
    DEFAULT_CALL_DISPOSITION_OPTIONS,
    CallDispositionOption,
)
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.pipecat.tracing_config import ensure_tracing
from api.services.workflow.conversation_history import (
    build_conversation_history,
    has_user_turns,
)
from api.utils.template_renderer import render_template

CALL_DISPOSITION_CONTEXT_KEY = "call_disposition"
DISPOSITION_EXTRACTION_TRACE_NAME = "disposition_extraction"

_SYSTEM_PROMPT = (
    "You classify the final business outcome of a conversation. Return ONLY a "
    "valid JSON object with one top-level key named call_disposition. Do not "
    "wrap the JSON in markdown."
)

#: Dograh's built-in business outcomes. This is a catalog for filters and
#: reporting, not a validation allowlist: a workflow may configure its own
#: values, such as ``call_rescheduled``.
DEFAULT_DISPOSITION_CODES: tuple[str, ...] = tuple(
    option.code for option in DEFAULT_CALL_DISPOSITION_OPTIONS
)


def _validate_disposition(
    value: object,
    allowed_codes: Iterable[str],
) -> str | None:
    """Return the configured code matching the model output, if any."""
    if not isinstance(value, str):
        return None

    canonical_codes = {code.casefold(): code for code in allowed_codes}
    return canonical_codes.get(value.strip().casefold())


class DispositionExtractionService:
    """Classify a completed conversation into one configured disposition."""

    def __init__(
        self,
        *,
        llm: Any,
        context: LLMContext,
        options: Sequence[CallDispositionOption],
        template_context: Mapping[str, Any],
    ) -> None:
        self._llm = llm
        self._context = context
        self._options = tuple(options)
        self._template_context = dict(template_context)

    async def extract(
        self,
        *,
        parent_context: Any = None,
        organization_id: int | None = None,
        workflow_run_id: int | None = None,
    ) -> str | None:
        """Return the configured disposition supported by the conversation."""
        if not self._options or self._llm is None or self._context is None:
            return None
        if not has_user_turns(self._context):
            logger.debug(
                "No user speech in the conversation; skipping disposition extraction"
            )
            return None

        try:
            messages = [{"role": "user", "content": self._build_user_prompt()}]
            inference_context = LLMContext()
            inference_context.set_messages(messages)
            response = await self._run_inference(
                inference_context,
                messages,
                parent_context=parent_context,
            )

            if response is None:
                logger.warning(
                    "Disposition extractor returned no response; keeping the fallback"
                )
                return None

            extracted = parse_llm_json(response)
            if not isinstance(extracted, dict):
                logger.warning(
                    "Disposition extractor returned non-object JSON; keeping the fallback"
                )
                return None

            return _validate_disposition(
                extracted.get(CALL_DISPOSITION_CONTEXT_KEY),
                (option.code for option in self._options),
            )
        except Exception as error:
            metadata = failure_metadata_for_processor(self._llm)
            log_failure(
                classify_exception(
                    error,
                    source=metadata.source,
                    provider=metadata.provider,
                    error_owner=metadata.error_owner,
                ),
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                node_name=DISPOSITION_EXTRACTION_TRACE_NAME,
            )
            return None

    def _build_user_prompt(self) -> str:
        options = [
            option.model_copy(
                update={
                    "description": render_template(
                        option.description,
                        self._template_context,
                    )
                }
            ).model_dump()
            for option in self._options
        ]
        choices = json.dumps(options, ensure_ascii=False, indent=2)
        conversation_history = build_conversation_history(self._context)
        return (
            "Set call_disposition to exactly one code from the configured options "
            "below. Return the code exactly as written, not its description. If "
            "the conversation does not support any option, set call_disposition "
            "to null. Never invent a new code.\n\n"
            f"Configured dispositions (JSON):\n{choices}\n\n"
            f"Conversation history:\n{conversation_history}"
        )

    async def _run_inference(
        self,
        inference_context: LLMContext,
        messages: list[dict[str, str]],
        *,
        parent_context: Any,
    ) -> str | None:
        if not ensure_tracing():
            return await self._llm.run_inference(
                inference_context,
                system_instruction=_SYSTEM_PROMPT,
            )

        tracer = trace.get_tracer("pipecat")
        with tracer.start_as_current_span(
            DISPOSITION_EXTRACTION_TRACE_NAME,
            context=parent_context,
        ) as span:
            response = await self._llm.run_inference(
                inference_context,
                system_instruction=_SYSTEM_PROMPT,
            )
            add_llm_span_attributes(
                span,
                service_name=self._llm.__class__.__name__,
                model=getattr(self._llm, "model_name", "unknown"),
                operation_name=DISPOSITION_EXTRACTION_TRACE_NAME,
                messages=[{"role": "system", "content": _SYSTEM_PROMPT}, *messages],
                output=json.dumps({"content": response}),
                stream=False,
                parameters={},
            )
            return response
