import asyncio
from dataclasses import dataclass

from loguru import logger
from openai import NOT_GIVEN

try:
    import litellm
except ImportError:
    raise ImportError(
        "Missing module: litellm. Install with: pip install litellm"
    )

from pipecat.services.openai.base_llm import BaseOpenAILLMService, OpenAILLMSettings
from pipecat.services.settings import NOT_GIVEN as _PIPECAT_NOT_GIVEN
from pipecat.services.settings import assert_given


def _strip_not_given(params: dict) -> dict:
    """Remove OpenAI/pipecat NOT_GIVEN sentinels that litellm can't handle."""
    return {
        k: v
        for k, v in params.items()
        if v is not NOT_GIVEN and v is not _PIPECAT_NOT_GIVEN
    }


@dataclass
class LiteLLMSettings(OpenAILLMSettings):
    pass


class LiteLLMLLMService(BaseOpenAILLMService):
    """LLM service that routes requests through the LiteLLM SDK.

    Uses litellm.acompletion() to call 100+ providers (OpenAI, Anthropic,
    Bedrock, Vertex, Groq, etc.) with a unified model string format.
    Provider API keys are read from environment variables.
    """

    Settings = LiteLLMSettings
    _settings: Settings

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        drop_params: bool = True,
        settings: Settings | None = None,
        **kwargs,
    ):
        default_settings = self.Settings(model="gpt-4.1")

        if model is not None:
            default_settings.model = model

        if settings is not None:
            default_settings.apply_update(settings)

        self._litellm_api_key = api_key
        self._litellm_api_base = api_base
        self._litellm_drop_params = drop_params

        super().__init__(
            api_key=api_key or "not-used",
            settings=default_settings,
            **kwargs,
        )

    def create_client(self, api_key=None, base_url=None, **kwargs):
        return None

    async def get_chat_completions(self, context):
        adapter = self.get_llm_adapter()
        logger.debug(
            f"{self}: Generating chat via LiteLLM SDK: model={self._settings.model}"
        )

        params_from_context = adapter.get_llm_invocation_params(
            context,
            system_instruction=assert_given(self._settings.system_instruction),
            convert_developer_to_user=not self.supports_developer_role,
        )

        params = _strip_not_given(self.build_chat_completion_params(params_from_context))

        if self._litellm_api_key:
            params["api_key"] = self._litellm_api_key
        if self._litellm_api_base:
            params["api_base"] = self._litellm_api_base

        params["drop_params"] = self._litellm_drop_params

        if self._retry_on_timeout:
            try:
                chunks = await asyncio.wait_for(
                    litellm.acompletion(**params),
                    timeout=self._retry_timeout_secs,
                )
                return chunks
            except (TimeoutError, Exception) as e:
                if "timeout" in str(e).lower() or isinstance(e, TimeoutError):
                    logger.debug(f"{self}: Retrying LiteLLM completion due to timeout")
                    chunks = await litellm.acompletion(**params)
                    return chunks
                raise
        else:
            chunks = await litellm.acompletion(**params)
            return chunks

    async def run_inference(self, context, max_tokens=None, system_instruction=None):
        effective_instruction = system_instruction or self._settings.system_instruction
        adapter = self.get_llm_adapter()
        invocation_params = adapter.get_llm_invocation_params(
            context,
            system_instruction=effective_instruction,
            convert_developer_to_user=not self.supports_developer_role,
        )

        params = _strip_not_given(self.build_chat_completion_params(invocation_params))
        params["stream"] = False
        params.pop("stream_options", None)

        if max_tokens is not None:
            if "max_completion_tokens" in params:
                params["max_completion_tokens"] = max_tokens
            else:
                params["max_tokens"] = max_tokens

        if self._litellm_api_key:
            params["api_key"] = self._litellm_api_key
        if self._litellm_api_base:
            params["api_base"] = self._litellm_api_base
        params["drop_params"] = self._litellm_drop_params

        response = await litellm.acompletion(**params)
        return response.choices[0].message.content
