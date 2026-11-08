import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai.errors import ClientError
from google.genai.types import (
    CachedContent,
    CachedContentUsageMetadata,
    GenerateContentResponse,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.google.llm import GoogleLLMSettings

from api.services.pipecat.prompt_cache import (
    CacheEntry,
    CacheSpec,
    PromptCacheRegistry,
    rejected_cached_content,
)
from api.services.pipecat.service_factory import DograhGoogleLLMService


def spec():
    return CacheSpec(
        1,
        2,
        "node",
        "google-api-key-digest",
        "gemini-3-flash-preview",
        "Static {{name}}",
        [],
    )


def test_cache_identity_includes_tenant_credentials_model_prompt_and_tool_config():
    original = spec()
    for updates in (
        {"organization_id": 9},
        {"scope": "vertex-project-region"},
        {"model": "other"},
        {"system_instruction": "changed"},
        {"tool_config": {"function_calling_config": {"mode": "NONE"}}},
    ):
        assert replace(original, **updates).key != original.key
    assert "Static" not in original.key


def client_error(code=400, message="CachedContent resource is not found"):
    return ClientError(
        code,
        {"error": {"code": code, "message": message, "status": "INVALID_ARGUMENT"}},
    )


@pytest.mark.parametrize(
    "code,message,expected",
    [
        (400, "CachedContent expired", True),
        (404, "cached_content missing", True),
        (429, "cached content quota", False),
        (400, "invalid tool schema", False),
    ],
)
def test_only_cache_resource_errors_are_retried(code, message, expected):
    assert rejected_cached_content(client_error(code, message)) == expected


def service(monkeypatch, *, cached=True):
    llm = DograhGoogleLLMService(
        api_key="test",
        settings=GoogleLLMSettings(
            model="gemini-3-flash-preview", system_instruction="Static {{name}}"
        ),
    )
    llm.prompt_cache_scope = "google-api-key-digest"
    llm.configure_prompt_cache(
        organization_id=1, workflow_id=2, node_id="node", enabled=True
    )
    llm.push_frame = AsyncMock()
    registry = SimpleNamespace(
        lookup=AsyncMock(
            return_value=CacheEntry("cachedContents/one", time.time() + 3000, 6000)
            if cached
            else None
        ),
        prewarm=Mock(),
        invalidate=AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.gemini_prompt_cache.get_prompt_cache_registry",
        lambda: registry,
    )
    llm._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content_stream=AsyncMock(),
                generate_content=AsyncMock(
                    return_value=GenerateContentResponse(candidates=[])
                ),
            )
        )
    )
    return llm, registry


async def chunks(error=None, after=False):
    if error and not after:
        raise error
    yield GenerateContentResponse()
    if error:
        raise error


@pytest.mark.asyncio
async def test_failed_cached_request_does_not_emit_cache_hit(monkeypatch):
    llm, _ = service(monkeypatch)
    llm._client.aio.models.generate_content_stream.return_value = chunks(
        client_error(429, "Quota exceeded")
    )
    with pytest.raises(ClientError):
        async for _ in await llm._stream_content(
            LLMContext([{"role": "user", "content": "Hi"}])
        ):
            pass
    llm.push_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_cold_recovery_scans_at_most_one_page_before_creating():
    scanned = []

    async def entries():
        for index in range(1000):
            scanned.append(index)
            yield CachedContent(
                name=f"cachedContents/{index}", display_name="unrelated"
            )

    redis = SimpleNamespace(
        exists=AsyncMock(return_value=0),
        hgetall=AsyncMock(return_value={}),
        set=AsyncMock(return_value=True),
        eval=AsyncMock(return_value=1),
    )
    caches = SimpleNamespace(
        list=AsyncMock(return_value=entries()),
        create=AsyncMock(
            return_value=CachedContent(
                name="cachedContents/new",
                expire_time=datetime.now(UTC) + timedelta(hours=1),
            )
        ),
    )
    await PromptCacheRegistry(redis)._ensure(
        spec(), SimpleNamespace(aio=SimpleNamespace(caches=caches))
    )
    assert len(scanned) == 100
    caches.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_cached_request_strips_only_static_fields_and_side_inference_stays_inline(
    monkeypatch,
):
    llm, registry = service(monkeypatch)
    llm._client.aio.models.generate_content_stream.return_value = chunks()
    context = LLMContext([{"role": "user", "content": "name: Ada"}])
    awaitable_stream = await llm._stream_content(context)
    assert len([chunk async for chunk in awaitable_stream]) == 1
    sent = llm._client.aio.models.generate_content_stream.call_args.kwargs["config"]
    assert sent.cached_content == "cachedContents/one"
    assert (
        sent.system_instruction is None
        and sent.tools is None
        and sent.tool_config is None
    )
    await llm.run_inference(
        LLMContext([{"role": "user", "content": "extract"}]),
        system_instruction="Extract variables",
    )
    side = llm._client.aio.models.generate_content.call_args.kwargs["config"]
    assert side.cached_content is None
    assert side.system_instruction == "Extract variables"
    assert llm._settings.system_instruction == "Static {{name}}"


@pytest.mark.asyncio
async def test_lazy_cache_error_retries_once_inline_with_identical_history(monkeypatch):
    llm, registry = service(monkeypatch)
    method = llm._client.aio.models.generate_content_stream
    method.side_effect = [chunks(client_error()), chunks()]
    context = LLMContext([{"role": "user", "content": "hello"}])
    assert len([chunk async for chunk in await llm._stream_content(context)]) == 1
    assert method.await_count == 2
    first, second = [call.kwargs for call in method.call_args_list]
    assert first["contents"] == second["contents"]
    assert second["config"].cached_content is None
    assert second["config"].system_instruction == "Static {{name}}"
    registry.invalidate.assert_awaited_once()
    assert llm.push_frame.call_args.args[0].data[0].cache_mode == "inline_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,after", [(client_error(429), False), (client_error(), True)]
)
async def test_does_not_retry_rate_limit_or_partial_output(monkeypatch, error, after):
    llm, registry = service(monkeypatch)
    method = llm._client.aio.models.generate_content_stream
    method.return_value = chunks(error, after=after)
    with pytest.raises(ClientError):
        [
            chunk
            async for chunk in await llm._stream_content(
                LLMContext([{"role": "user", "content": "hello"}])
            )
        ]
    assert method.await_count == 1
    registry.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_kill_switch_bypasses_registry_and_preserves_inline_request(monkeypatch):
    llm, registry = service(monkeypatch)
    monkeypatch.setenv("DOGRAH_PROMPT_CACHE_DISABLED", "1")
    llm._client.aio.models.generate_content_stream.return_value = chunks()
    [
        chunk
        async for chunk in await llm._stream_content(
            LLMContext([{"role": "user", "content": "hello"}])
        )
    ]
    config = llm._client.aio.models.generate_content_stream.call_args.kwargs["config"]
    assert (
        config.cached_content is None and config.system_instruction == "Static {{name}}"
    )
    registry.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_requests_match_the_original_google_stream(monkeypatch):
    from pipecat.services.google.llm import GoogleLLMService

    llm, registry = service(monkeypatch)
    llm.configure_prompt_cache(
        organization_id=1, workflow_id=2, node_id="node", enabled=False
    )
    method = llm._client.aio.models.generate_content_stream
    method.side_effect = lambda **kwargs: chunks()
    context = LLMContext([{"role": "user", "content": "hello"}])
    [chunk async for chunk in await GoogleLLMService._stream_content(llm, context)]
    baseline = method.call_args.kwargs
    [chunk async for chunk in await llm._stream_content(context)]
    assert method.call_args.kwargs == baseline
    registry.lookup.assert_not_awaited()
    assert llm.push_frame.call_args.args[0].data[0].cache_mode == "off"


@pytest.mark.asyncio
async def test_registry_failure_is_a_miss():
    redis = SimpleNamespace(hgetall=AsyncMock(side_effect=TimeoutError))
    assert await PromptCacheRegistry(redis).lookup(spec()) is None


@pytest.mark.asyncio
async def test_recreation_failure_still_retries_inline(monkeypatch):
    llm, registry = service(monkeypatch)
    registry.invalidate.side_effect = RuntimeError("Registry unavailable")
    method = llm._client.aio.models.generate_content_stream
    method.side_effect = [chunks(client_error()), chunks()]
    assert (
        len(
            [
                chunk
                async for chunk in await llm._stream_content(
                    LLMContext([{"role": "user", "content": "hello"}])
                )
            ]
        )
        == 1
    )
    assert method.await_count == 2


@pytest.mark.asyncio
async def test_missing_spec_does_not_retry_noncache_errors(monkeypatch):
    llm, _ = service(monkeypatch)
    llm._cache_spec = Mock(return_value=None)
    method = llm._client.aio.models.generate_content_stream
    method.side_effect = client_error(429)
    with pytest.raises(ClientError):
        await llm._stream_content(LLMContext([{"role": "user", "content": "hello"}]))
    assert method.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,message,marker,reason,delay",
    [
        (
            400,
            "Cached content is too small; minimum token count is 4096",
            "backoff",
            "too_small",
            3600,
        ),
        (
            400,
            "Caching is not supported for this model",
            "backoff",
            "unsupported",
            3600,
        ),
        (400, "Tool config is not supported", "backoff", "unsupported", 3600),
        (429, "Quota exceeded", "backoff", "error", 60),
    ],
)
async def test_creation_failure_backoff(
    monkeypatch, code, message, marker, reason, delay
):
    redis = SimpleNamespace(
        exists=AsyncMock(return_value=0),
        hgetall=AsyncMock(return_value={}),
        set=AsyncMock(return_value=True),
        eval=AsyncMock(),
    )
    caches = SimpleNamespace(list=AsyncMock(side_effect=client_error(code, message)))
    registry = PromptCacheRegistry(redis)
    await registry._ensure(spec(), SimpleNamespace(aio=SimpleNamespace(caches=caches)))
    redis.set.assert_any_await(
        spec().model_key if marker == "model" else spec().key + ":backoff",
        reason,
        ex=delay,
    )
    assert not any(
        call.args[0] == spec().model_key for call in redis.set.await_args_list
    )


@pytest.mark.asyncio
async def test_fallback_models_prewarm_independent_keys(monkeypatch):
    from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

    from api.services.pipecat.service_factory import create_llm_service
    from api.tests.test_llm_fallback_configuration import configuration

    registry = SimpleNamespace(prewarm=Mock())
    monkeypatch.setattr(
        "api.services.pipecat.gemini_prompt_cache.get_prompt_cache_registry",
        lambda: registry,
    )
    llm = create_llm_service(
        configuration(
            error_target={
                "provider": "google",
                "api_key": "backup",
                "model": "gemini-2.5-flash",
            }
        )
    )
    try:
        llm.configure_prompt_cache(
            organization_id=1, workflow_id=2, node_id="n", enabled=True
        )
        llm.prewarm_prompt_cache(
            node_id="n",
            system_instruction="Static {{name}}",
            tools=ToolsSchema(standard_tools=[]),
        )
        primary, backup = [call.args[0] for call in registry.prewarm.call_args_list]
        assert primary.model != backup.model
        assert primary.key != backup.key and primary.scope != backup.scope
    finally:
        await llm.cleanup()


@pytest.mark.asyncio
async def test_prewarm_single_flight_and_sliding_refresh():
    redis = SimpleNamespace(
        exists=AsyncMock(return_value=0),
        hgetall=AsyncMock(return_value={}),
        set=AsyncMock(return_value=True),
        eval=AsyncMock(return_value=1),
    )
    resource = CachedContent(
        name="cachedContents/new",
        expire_time=datetime.now(UTC) + timedelta(hours=1),
        usage_metadata=CachedContentUsageMetadata(total_token_count=6000),
    )

    async def empty():
        if False:
            yield

    caches = SimpleNamespace(
        list=AsyncMock(return_value=empty()),
        create=AsyncMock(return_value=resource),
        update=AsyncMock(return_value=resource),
    )
    registry = PromptCacheRegistry(redis)
    client = SimpleNamespace(aio=SimpleNamespace(caches=caches))
    for _ in range(60):
        registry.prewarm(spec(), client)
    await asyncio.gather(*registry.tasks.values())
    caches.create.assert_awaited_once()
    assert (await registry.lookup(spec())).tokens == 6000
    registry.local.clear()
    redis.hgetall.return_value = {
        "name": "cachedContents/new",
        "expires": str(time.time() + 100),
        "tokens": "6000",
    }
    await registry._ensure(spec(), client)
    caches.update.assert_awaited_once()
