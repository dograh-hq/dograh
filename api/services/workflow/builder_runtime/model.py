"""Dograh's agent model adapter; MPS owns inference and provider routing."""

import asyncio
import json

import httpx
from fastapi import HTTPException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from api.errors.failure import (
    ErrorSource,
    classify_exception,
    extract_http_status,
    log_failure,
)
from api.errors.mps import MPSUnavailableError
from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.managed_model_services import get_dograh_service_api_key
from api.services.mps_service_key_client import mps_service_key_client

from .errors import BuilderModelError
from .tracing import builder_trace_metadata

BUILDER_USAGE_CONTEXT = "workflow_builder"


async def _completion_with_retry(**kwargs):
    # Only inference is retried here; tool mutations have separate durable receipts.
    for attempt in range(3):
        try:
            return await mps_service_key_client.create_agent_builder_completion(
                **kwargs
            )
        except (httpx.HTTPError, MPSUnavailableError) as exc:
            status = extract_http_status(exc)
            transient = isinstance(exc, httpx.TransportError) or status in {
                408,
                429,
                500,
                502,
                503,
                504,
            }
            if not transient or attempt == 2:
                raise
            await asyncio.sleep(0.5 * (2**attempt))


class BuilderChatModel(BaseChatModel):
    # A tracing label. MPS's Agent Builder endpoint owns model selection.
    model_name: str = BUILDER_USAGE_CONTEXT
    max_output_tokens: int = 32000
    organization_id: int
    user_id: str

    @property
    def _llm_type(self) -> str:
        return "dograh_builder"

    def _get_ls_params(self, **kwargs):
        return {**super()._get_ls_params(**kwargs), "ls_provider": "dograh_builder"}

    def bind_tools(self, tools, **kwargs):
        return self.bind(
            tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs
        )

    def _generate(self, *args, **kwargs):
        raise NotImplementedError("The builder uses async model calls")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # Resolve on inference, so configuration changes apply to resumed sessions
        # and credentials never enter the model's serialized fields/checkpoints.
        resolved = await get_resolved_ai_model_configuration(
            organization_id=self.organization_id
        )
        api_key = (get_dograh_service_api_key(resolved.effective) or "").strip()
        if not api_key:
            raise HTTPException(
                400,
                "Agent builder is currently only supported with a Dograh Service Key. "
                "Set it in /model-configurations.",
            )
        wire = convert_to_openai_messages(messages)
        for original, converted in zip(messages, wire):
            if original.type == "ai":
                # Preserve provider reasoning/tool metadata across tool turns.
                converted.update(original.additional_kwargs.get("mps_message", {}))
        try:
            response = await _completion_with_retry(
                service_key=api_key,
                messages=wire,
                # Reasoning models (gpt-5, o-series, R1, …) count their
                # reasoning tokens against this budget, so a small cap starves
                # the actual tool-call output and truncates its JSON arguments
                # mid-string. Give enough headroom for reasoning + the full
                # tool call (e.g. a complete @dograh/sdk workflow in
                # propose_workflow's args).
                max_tokens=self.max_output_tokens,
                tools=kwargs.get("tools"),
                tool_choice=kwargs.get("tool_choice"),
                metadata={
                    **builder_trace_metadata(),
                    "usage_context": BUILDER_USAGE_CONTEXT,
                    **(
                        {"builder_stage": kwargs["authoring_stage"]}
                        if kwargs.get("authoring_stage")
                        else {}
                    ),
                    # Identifiers for tracing only, never authorization.
                    "source": BUILDER_USAGE_CONTEXT,
                    "dograh_organization_id": str(self.organization_id),
                    "dograh_user_id": self.user_id,
                },
            )
        except (httpx.HTTPError, MPSUnavailableError) as exc:
            failure = classify_exception(exc, source=ErrorSource.LLM, provider="dograh")
            log_failure(
                failure,
                organization_id=self.organization_id,
                operation="workflow_builder.create_agent_builder_completion",
                **builder_trace_metadata(),
            )
            # Keep the shared code/status, but never expose upstream response
            # bodies, which can contain prompts or provider credentials.
            raise BuilderModelError(
                failure, upstream_status_code=extract_http_status(exc)
            ) from exc
        choice = response["choices"][0]
        raw = choice["message"]
        if choice.get("finish_reason") == "length":
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            additional_kwargs={
                                "builder_repair": "The response reached its output limit. No tools from that response executed. Use smaller exact edits instead of resending full source, and reissue complete tool arguments."
                            },
                        )
                    )
                ],
                llm_output={"usage": response.get("usage", {})},
            )
        calls = []
        for call in raw.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"]["arguments"])
                if not isinstance(arguments, dict):
                    raise TypeError("Tool arguments must be an object")
            except (ValueError, TypeError):
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                additional_kwargs={
                                    "builder_repair": "The previous tool arguments were not a valid JSON object. No tools from that response executed. Reissue the call with complete JSON arguments."
                                },
                            )
                        )
                    ],
                    llm_output={"usage": response.get("usage", {})},
                )
            calls.append(
                {
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "args": arguments,
                    "type": "tool_call",
                }
            )
        extras = {
            k: v
            for k, v in raw.items()
            if k not in ("role", "content") and v is not None
        }
        message = AIMessage(
            content=raw.get("content") or "",
            tool_calls=calls,
            additional_kwargs={"mps_message": extras},
            response_metadata={"model_name": response.get("model", self.model_name)},
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"usage": response.get("usage", {})},
        )
