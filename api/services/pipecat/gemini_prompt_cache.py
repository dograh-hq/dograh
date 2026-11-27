"""Gemini explicit caching for conversations and opted-in extraction requests."""

from __future__ import annotations

from contextlib import aclosing
from typing import Literal

from google.genai.types import GenerateContentConfig
from loguru import logger

from api.services.pipecat.prompt_cache import (
    CacheSpec,
    cache_disabled,
    count_cache_event,
    get_prompt_cache_registry,
    rejected_cached_content,
)
from api.services.pipecat.usage_metrics import InferenceUsage
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, MetricsData
from pipecat.utils.types import assert_given


class PromptCacheMetricsData(MetricsData):
    node_id: str
    cache_mode: Literal["explicit", "inline_not_ready", "inline_fallback", "off"]


class GeminiPromptCacheMixin:
    """Keep conversation and extraction caches separate without mutating settings."""

    _prompt_cache_binding: tuple[int, int, str] | None = None
    _prompt_cache_node_id: str = ""
    prompt_cache_scope: str | None = None
    _inference_usage: InferenceUsage | None = None
    _inference_cache_binding: tuple[int, int, str] | None = None

    def configure_inference(
        self,
        *,
        usage: InferenceUsage,
        organization_id: int,
        workflow_id: int,
        node_id: str,
        cache_enabled: bool,
    ) -> None:
        """Opt a dedicated completion client into usage reporting and static caching."""
        self._inference_usage = usage
        self._inference_cache_binding = (
            (organization_id, workflow_id, f"extraction:{node_id}")
            if cache_enabled
            else None
        )

    async def run_inference(
        self, context, max_tokens=None, system_instruction=None, response_schema=None
    ):
        if self._inference_usage is None:
            return await super().run_inference(
                context, max_tokens, system_instruction, response_schema
            )

        model = assert_given(self._settings.model)
        params = await self.get_llm_adapter().get_llm_invocation_params(
            context,
            system_instruction=system_instruction
            or assert_given(self._settings.system_instruction),
            ensure_last_message_is_user=self._should_inject_trailing_user_message(),
        )
        inline = self._build_generation_params(
            system_instruction=params["system_instruction"],
            tools=params["tools"] or None,
        )
        if max_tokens is not None:
            inline["max_output_tokens"] = max_tokens
        response_schema = self._check_response_schema(response_schema)
        if response_schema is not None:
            inline.update(
                response_mime_type="application/json",
                response_json_schema=response_schema,
            )

        binding = self._inference_cache_binding
        spec, entry, registry = None, None, None
        mode = "off"
        if binding and self.prompt_cache_scope and not cache_disabled():
            mode = "inline_not_ready"
            spec = CacheSpec(
                *binding,
                self.prompt_cache_scope,
                model,
                inline.get("system_instruction"),
                inline.get("tools") or [],
                inline.get("tool_config"),
            )
            try:
                registry = get_prompt_cache_registry()
                entry = await registry.prepare_inference(
                    spec, self._client, credentials=getattr(self, "_credentials", None)
                )
            except Exception:
                logger.warning("Extraction cache unavailable; sending inline request")
        config = dict(inline)
        if entry:
            for field in ("system_instruction", "tools", "tool_config"):
                config.pop(field, None)
            config["cached_content"] = entry.name
            mode = "explicit"

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=params["messages"],
                config=GenerateContentConfig(**config),
            )
        except Exception as error:
            if not entry or not rejected_cached_content(error):
                raise
            try:
                await registry.invalidate(spec, entry)
            except Exception:
                logger.warning("Extraction cache invalidation unavailable")
            mode = "inline_fallback"
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=params["messages"],
                config=GenerateContentConfig(**inline),
            )

        # Record before parsing/finish-reason handling: invalid answers still cost tokens.
        usage = response.usage_metadata
        if usage is not None:
            prompt = (usage.prompt_token_count or 0) + (
                usage.tool_use_prompt_token_count or 0
            )
            reasoning = usage.thoughts_token_count or 0
            completion = (usage.candidates_token_count or 0) + reasoning
            self._inference_usage.record(
                self.name,
                model,
                LLMTokenUsage(
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=prompt + completion,
                    cache_read_input_tokens=usage.cached_content_token_count or 0,
                    reasoning_tokens=reasoning,
                ),
            )
        count_cache_event("hit" if mode == "explicit" else mode)
        self._inference_usage.prompt_cache.append(
            {
                "processor": self.name,
                "model": model,
                "node_id": binding[2] if binding else "",
                "cache_mode": mode,
            }
        )
        if response.candidates and response.candidates[0].finish_reason:
            self._handle_finish_reason(response.candidates[0].finish_reason)
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts or []:
                if part.text and not part.thought:
                    return part.text
        return None

    def configure_prompt_cache(
        self, *, organization_id: int, workflow_id: int, node_id: str, enabled: bool
    ) -> None:
        self._prompt_cache_node_id = node_id
        self._prompt_cache_binding = (
            (organization_id, workflow_id, node_id) if enabled else None
        )

    def _cache_spec(self, system_instruction, tools, *, node_id=None, tool_config=None):
        if not self._prompt_cache_binding or not self.prompt_cache_scope:
            return None
        org_id, workflow_id, current_node_id = self._prompt_cache_binding
        if not org_id or not system_instruction:
            return None
        return CacheSpec(
            org_id,
            workflow_id,
            node_id or current_node_id,
            self.prompt_cache_scope,
            assert_given(self._settings.model),
            system_instruction,
            tools,
            tool_config,
        )

    def prompt_cache_spec_for_node(
        self, *, node_id: str, system_instruction: str, tools
    ) -> CacheSpec | None:
        provider_tools = self.get_llm_adapter().to_provider_tools_format(tools)
        effective = self._build_generation_params(
            system_instruction=system_instruction,
            tools=provider_tools,
            tool_config=self._tool_config or None,
        )
        return self._cache_spec(
            effective.get("system_instruction"),
            effective.get("tools") or [],
            node_id=node_id,
            tool_config=effective.get("tool_config"),
        )

    def prewarm_prompt_cache(
        self, *, node_id: str, system_instruction: str, tools
    ) -> list:
        if cache_disabled():
            return []
        spec = self.prompt_cache_spec_for_node(
            node_id=node_id, system_instruction=system_instruction, tools=tools
        )
        if spec:
            task = get_prompt_cache_registry().prewarm(
                spec, self._client, credentials=getattr(self, "_credentials", None)
            )
            return [task] if task is not None else []
        return []

    async def _cache_metric(self, mode, node_id):
        try:
            count_cache_event("hit" if mode == "explicit" else mode)
            await self.push_frame(
                MetricsFrame(
                    data=[
                        PromptCacheMetricsData(
                            processor=self.name,
                            model=assert_given(self._settings.model),
                            node_id=node_id,
                            cache_mode=mode,
                        )
                    ]
                )
            )
        except Exception:
            logger.warning("Prompt cache metric unavailable")

    async def _stream_content(self, context):
        binding = self._prompt_cache_binding
        if not binding or not self.prompt_cache_scope or cache_disabled():
            await self._cache_metric("off", self._prompt_cache_node_id)
            return await super()._stream_content(context)

        adapter = self.get_llm_adapter()
        params = await adapter.get_llm_invocation_params(
            context,
            system_instruction=assert_given(self._settings.system_instruction),
            ensure_last_message_is_user=self._should_inject_trailing_user_message(),
        )
        tools = params["tools"] or self._tools or []
        # Build both variants from the same snapshot, preserving the exact
        # settings, tools and history if the cached request needs a retry.
        inline = self._build_generation_params(
            system_instruction=params["system_instruction"],
            tools=tools,
            tool_config=self._tool_config or None,
        )
        spec = None
        try:
            spec = self._cache_spec(
                inline.get("system_instruction"),
                inline.get("tools") or [],
                tool_config=inline.get("tool_config"),
            )
            if spec:
                registry = get_prompt_cache_registry()
                entry = await registry.lookup(spec)
                registry.prewarm(
                    spec, self._client, credentials=getattr(self, "_credentials", None)
                )
        except Exception:
            logger.warning("Prompt cache lookup unavailable; sending inline request")
            spec = None
        if not spec:
            await self._cache_metric("inline_not_ready", binding[2])
            return await super()._stream_content(context)
        self._log_llm_response(context)
        mode = "explicit" if entry else "inline_not_ready"
        cached = dict(inline)
        if entry:
            for key in ("system_instruction", "tools", "tool_config"):
                cached.pop(key, None)
            cached["cached_content"] = entry.name

        async def iterate():
            nonlocal mode
            yielded = False
            reported = False
            try:
                stream = await self._client.aio.models.generate_content_stream(
                    model=spec.model,
                    contents=params["messages"],
                    config=GenerateContentConfig(**cached),
                )
                async with aclosing(stream):
                    async for chunk in stream:
                        if not reported:
                            await self._cache_metric(mode, spec.node_id)
                            reported = True
                        yielded = True
                        yield chunk
            except Exception as error:
                if not entry or yielded or not rejected_cached_content(error):
                    raise
                try:
                    await registry.invalidate(spec, entry)
                    registry.prewarm(
                        spec,
                        self._client,
                        credentials=getattr(self, "_credentials", None),
                        recreate=True,
                    )
                except Exception:
                    logger.warning("Prompt cache recreation unavailable")
                mode = "inline_fallback"
                logger.bind(cache_key=spec.key, node_id=spec.node_id).warning(
                    "Prompt cache rejected; retrying inline"
                )
                stream = await self._client.aio.models.generate_content_stream(
                    model=spec.model,
                    contents=params["messages"],
                    config=GenerateContentConfig(**inline),
                )
                async with aclosing(stream):
                    async for chunk in stream:
                        if not reported:
                            await self._cache_metric(mode, spec.node_id)
                            reported = True
                        yield chunk
            finally:
                if not reported and mode != "explicit":
                    await self._cache_metric(mode, spec.node_id)

        return iterate()
