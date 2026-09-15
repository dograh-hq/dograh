"""Pipecat Responses parsing backed only by an organization-bound subscription."""

import asyncio

from api.services.configuration.openai_subscription_responses import (
    SubscriptionResponsesClient,
)
from api.services.pipecat.usage_metrics import SubscriptionReasoningUsageMetricsData
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.openai.responses.llm import OpenAIResponsesHttpLLMService
from pipecat.utils.types import assert_given


class SubscriptionResponsesLLMService(OpenAIResponsesHttpLLMService):
    """Reuse pinned tool/SSE handling without constructing a public-API client."""

    def __init__(
        self,
        *,
        auth_service,
        organization_id: int,
        settings,
        owns_auth_service=False,
        **kwargs
    ):
        auth_service.assert_organization(organization_id)
        self._auth_service = auth_service
        self._owns_auth_service = owns_auth_service
        self._close_task = None
        self._subscription_client = SubscriptionResponsesClient(
            auth_service, organization_id
        )
        super().__init__(settings=settings, retry_on_timeout=False, **kwargs)

    async def aclose(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _close_owned(self):
        try:
            await self._subscription_client.aclose()
        finally:
            if self._owns_auth_service:
                await self._auth_service.aclose()

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            await self.aclose()

    def _create_client(self, **kwargs):
        # The base class resolves ambient OPENAI_API_KEY before this hook.
        # Discard it and never instantiate AsyncOpenAI or use its API endpoint.
        self._api_key = None

    def bind_account(self, account_id: str):
        self._subscription_client.bind_account(account_id)

    async def _create_stream(self, params: dict):
        return await self._subscription_client.stream(params)

    async def run_inference(
        self,
        context: LLMContext,
        max_tokens: int | None = None,
        system_instruction: str | None = None,
    ) -> str | None:
        instruction = system_instruction or assert_given(
            self._settings.system_instruction
        )
        params = self._build_response_params(
            self.get_llm_adapter().get_llm_invocation_params(
                context,
                system_instruction=instruction,
            )
        )
        if max_tokens is not None:
            params["max_output_tokens"] = max_tokens
        response = await self._subscription_client.complete(params)
        if response.usage is not None:
            usage = response.usage
            await self.start_llm_usage_metrics(
                LLMTokenUsage(
                    prompt_tokens=usage.input_tokens or 0,
                    completion_tokens=usage.output_tokens or 0,
                    total_tokens=usage.total_tokens or 0,
                    cache_read_input_tokens=(
                        (usage.input_tokens_details.cached_tokens or 0)
                        if usage.input_tokens_details
                        else 0
                    ),
                    reasoning_tokens=(
                        (usage.output_tokens_details.reasoning_tokens or 0)
                        if usage.output_tokens_details
                        else 0
                    ),
                )
            )
        return response.output_text

    async def start_llm_usage_metrics(self, tokens: LLMTokenUsage):
        # Public API pricing must never consume subscription token observations.
        if self._setup is None:
            return
        await self.push_frame(
            MetricsFrame(
                data=[
                    SubscriptionReasoningUsageMetricsData(
                        processor=self.name,
                        model=assert_given(self._settings.model),
                        value=tokens,
                    )
                ]
            )
        )
