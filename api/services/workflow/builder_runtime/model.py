"""Dograh's agent model adapter; MPS owns inference and provider routing."""

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


class BuilderChatModel(BaseChatModel):
    # A tier, never a concrete provider/model. MPS resolves metadata.usage_context.
    model_name: str = BUILDER_USAGE_CONTEXT
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
            response = await mps_service_key_client.create_chat_completion(
                service_key=api_key,
                model=self.model_name,
                messages=wire,
                # Reasoning models (gpt-5, o-series, R1, …) count their
                # reasoning tokens against this budget, so a small cap starves
                # the actual tool-call output and truncates its JSON arguments
                # mid-string. Give enough headroom for reasoning + the full
                # tool call (e.g. a complete @dograh/sdk workflow in
                # propose_workflow's args).
                max_tokens=32000,
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
            if isinstance(exc, MPSUnavailableError) and exc.status_code is None:
                # This client raises MPSUnavailableError when the successful
                # correlation response lacks an ID; the connection did succeed.
                failure.code = "dograh-invalid-response"
                failure.retryable = None
            log_failure(
                failure,
                organization_id=self.organization_id,
                operation="workflow_builder.create_chat_completion",
                **builder_trace_metadata(),
            )
            # Keep the shared code/status, but never expose upstream response
            # bodies, which can contain prompts or provider credentials.
            raise BuilderModelError(
                failure, upstream_status_code=extract_http_status(exc)
            ) from exc
        raw = response["choices"][0]["message"]
        calls = []
        for call in raw.get("tool_calls") or []:
            calls.append(
                {
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "args": json.loads(call["function"]["arguments"]),
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
