"""Gemini explicit caching restricted to streaming conversation requests."""

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
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import MetricsData
from pipecat.utils.types import assert_given


class PromptCacheMetricsData(MetricsData):
    node_id: str
    cache_mode: Literal["explicit", "inline_not_ready", "inline_fallback", "off"]


class GeminiPromptCacheMixin:
    """Never mutate settings.extra, context, or out-of-band run_inference."""

    _prompt_cache_binding: tuple[int, int, str] | None = None
    _prompt_cache_node_id: str = ""
    prompt_cache_scope: str | None = None

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
    ) -> None:
        if cache_disabled():
            return
        spec = self.prompt_cache_spec_for_node(
            node_id=node_id, system_instruction=system_instruction, tools=tools
        )
        if spec:
            get_prompt_cache_registry().prewarm(
                spec, self._client, credentials=getattr(self, "_credentials", None)
            )

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
                if not reported:
                    await self._cache_metric(mode, spec.node_id)

        return iterate()
