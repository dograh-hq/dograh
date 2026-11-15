import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai.types import GenerateContentResponse
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.llm_fallback import ErrorCondition
from api.services.pipecat.fallback_llm import FallbackLLMProcessor, FallbackRoute
from api.services.pipecat.prompt_cache import CacheEntry, PromptCacheRegistry
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.pipecat.usage_metrics import InferenceUsage
from api.services.workflow import final_extraction
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine_variable_extractor import extract_variables
from api.tests.test_final_extraction import worker as worker
from api.tests.test_prompt_cache import client_error, spec


def response(text='{"name":"Ada"}'):
    return GenerateContentResponse(
        candidates=[{"content": {"parts": [{"text": text}]}}],
        usage_metadata={
            "prompt_token_count": 3000,
            "cached_content_token_count": 2700,
            "candidates_token_count": 20,
            "thoughts_token_count": 30,
            "total_token_count": 3050,
        },
    )


@pytest.fixture(params=["google", "google_vertex"])
def extraction_service(monkeypatch, request):
    monkeypatch.setenv("DOGRAH_PROMPT_CACHE_DISABLED", "0")
    monkeypatch.setattr(
        "api.services.pipecat.service_factory.prepare_google_credentials", AsyncMock()
    )
    llm = create_llm_service_from_provider(
        provider=request.param,
        model="gemini-3-flash-preview",
        api_key="test",
        project_id="test-project",
        location="global",
        credentials='{"key":"test"}',
        enable_direct_mode=True,
    )
    usage = InferenceUsage()
    llm.configure_inference(
        usage=usage,
        organization_id=7,
        workflow_id=9,
        node_id="last",
        cache_enabled=True,
    )
    registry = SimpleNamespace(
        prepare_inference=AsyncMock(
            return_value=CacheEntry("cachedContents/one", time.time() + 3600, 2700)
        ),
        invalidate=AsyncMock(),
    )
    monkeypatch.setattr(
        "api.services.pipecat.gemini_prompt_cache.get_prompt_cache_registry",
        lambda: registry,
    )
    llm._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock(return_value=response()))
        )
    )
    llm.cleanup = AsyncMock()
    return llm, usage, registry


@pytest.mark.asyncio
async def test_extraction_reuses_only_definitions_and_reports_usage(extraction_service):
    llm, usage, registry = extraction_service
    variables = [
        ExtractionVariableDTO(name="name", type="string", prompt="Customer name")
    ]
    for conversation in ("My name is Ada", "My name is Grace"):
        assert await extract_variables(
            llm,
            variables,
            conversation,
            extraction_prompt="Extract accurately",
            separate_static_prompt=True,
            strict=True,
        ) == {"name": "Ada"}
    specs = [call.args[0] for call in registry.prepare_inference.await_args_list]
    assert specs[0].key == specs[1].key
    assert specs[0].organization_id == 7
    assert specs[0].workflow_id == 9
    assert specs[0].node_id == "extraction:last"
    assert "Customer name" in specs[0].system_instruction
    assert "Extract accurately" in specs[0].system_instruction
    assert "Ada" not in specs[0].system_instruction
    assert "Grace" not in specs[0].system_instruction
    requests = llm._client.aio.models.generate_content.await_args_list
    for call in requests:
        assert call.kwargs["config"].cached_content == "cachedContents/one"
        assert call.kwargs["config"].system_instruction is None
        assert "Customer name" not in str(call.kwargs["contents"])
    assert "Ada" in str(requests[0].kwargs["contents"])
    assert "Grace" in str(requests[1].kwargs["contents"])
    tokens = next(iter(usage.llm.values()))
    assert tokens["prompt_tokens"] == 6000
    assert tokens["completion_tokens"] == 100
    assert tokens["reasoning_tokens"] == 60
    assert tokens["cache_read_input_tokens"] == 5400
    assert tokens["total_tokens"] == 6100
    assert [item["cache_mode"] for item in usage.prompt_cache] == [
        "explicit",
        "explicit",
    ]
    variables[0].prompt = "Different definition"
    await extract_variables(llm, variables, "Ada", separate_static_prompt=True)
    assert registry.prepare_inference.call_args.args[0].key != specs[0].key


@pytest.mark.asyncio
async def test_rejected_cache_retries_inline_preserving_options(extraction_service):
    llm, usage, registry = extraction_service
    generate = llm._client.aio.models.generate_content
    generate.side_effect = [client_error(), response()]
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    await llm.run_inference(
        LLMContext([{"role": "user", "content": "Ada"}]),
        system_instruction="Extract name",
        max_tokens=123,
        response_schema=schema,
    )
    first, retry = [call.kwargs for call in generate.await_args_list]
    assert first["contents"] == retry["contents"]
    assert first["config"].cached_content == "cachedContents/one"
    assert retry["config"].cached_content is None
    assert retry["config"].system_instruction == "Extract name"
    assert first["config"].max_output_tokens == retry["config"].max_output_tokens == 123
    assert (
        first["config"].response_json_schema
        == retry["config"].response_json_schema
        == schema
    )
    registry.invalidate.assert_awaited_once()
    assert next(iter(usage.llm.values()))["prompt_tokens"] == 3000
    assert usage.prompt_cache[0]["cache_mode"] == "inline_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["disabled", "not_ready", "unavailable", "dynamic"])
async def test_cache_ineligible_requests_still_report_usage(
    extraction_service, monkeypatch, reason
):
    llm, usage, registry = extraction_service
    if reason == "disabled":
        monkeypatch.setenv("DOGRAH_PROMPT_CACHE_DISABLED", "1")
    elif reason == "dynamic":
        llm.configure_inference(
            usage=usage,
            organization_id=7,
            workflow_id=9,
            node_id="last",
            cache_enabled=False,
        )
    elif reason == "not_ready":
        registry.prepare_inference.return_value = None
    else:
        registry.prepare_inference.side_effect = RuntimeError("Redis unavailable")
    await llm.run_inference(
        LLMContext([{"role": "user", "content": "Ada"}]),
        system_instruction="Extract name",
    )
    sent = llm._client.aio.models.generate_content.call_args.kwargs["config"]
    assert sent.cached_content is None
    assert sent.system_instruction == "Extract name"
    assert next(iter(usage.llm.values()))["total_tokens"] == 3050
    if reason in ("disabled", "dynamic"):
        registry.prepare_inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_does_not_claim_cache_hit(extraction_service):
    llm, usage, registry = extraction_service
    llm._client.aio.models.generate_content.side_effect = client_error(
        429, "Quota exceeded"
    )
    with pytest.raises(Exception, match="Quota exceeded"):
        await llm.run_inference(
            LLMContext([{"role": "user", "content": "Ada"}]),
            system_instruction="Extract name",
        )
    assert not usage.prompt_cache
    assert not usage.llm
    registry.invalidate.assert_not_awaited()
    assert llm._client.aio.models.generate_content.await_count == 1


@pytest.mark.asyncio
async def test_preparation_timeout_joins_cache_work(monkeypatch):
    registry = PromptCacheRegistry(Mock())
    registry.lookup = AsyncMock(return_value=None)
    stopped = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    registry._ensure = slow
    # Exercise the actual timeout path without waiting two seconds.
    real_timeout = asyncio.timeout
    monkeypatch.setattr(
        "api.services.pipecat.prompt_cache.asyncio.timeout",
        lambda _: real_timeout(0.01),
    )
    assert await registry.prepare_inference(spec(), Mock()) is None
    assert stopped.is_set()
    assert not registry.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeds", [True, False])
async def test_final_worker_persists_all_reported_attempts(
    worker, extraction_service, succeeds
):
    run, _, saved = worker
    llm, _, registry = extraction_service
    run.logs["final_extraction"]["requests"][0]["cache_static_prompt"] = True
    final_extraction.create_llm_service.return_value = llm
    llm._client.aio.models.generate_content.side_effect = (
        [response("invalid JSON"), response()]
        if succeeds
        else [response("invalid JSON")] * 3
    )
    await final_extraction.run_final_extraction(1, 7, "lease")
    record = saved.call_args.kwargs
    assert record["status"] == ("done" if succeeds else "failed")
    attempts = 2 if succeeds else 3
    assert (
        next(iter(record["usage_info"]["llm"].values()))["prompt_tokens"]
        == attempts * 3000
    )
    assert len(record["usage_info"]["prompt_cache"]) == attempts
    prepared = registry.prepare_inference.call_args.args[0]
    assert "- name (VariableType.string): Name" in prepared.system_instruction
    assert "Ada" not in prepared.system_instruction
    assert prepared.organization_id == 7
    assert prepared.workflow_id == 9
    llm.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_keeps_usage_from_both_completed_attempts(extraction_service):
    primary, _, registry = extraction_service
    backup = create_llm_service_from_provider(
        provider="google",
        model="gemini-2.5-flash",
        api_key="test",
        enable_direct_mode=True,
    )
    backup._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock(return_value=response()))
        )
    )
    primary._client.aio.models.generate_content.return_value = response("")
    fallback = FallbackLLMProcessor(
        primary, routes=[FallbackRoute(ErrorCondition(), backup)]
    )
    usage = InferenceUsage()
    fallback.configure_inference(
        usage=usage,
        organization_id=7,
        workflow_id=9,
        node_id="last",
        cache_enabled=True,
    )
    assert (
        await asyncio.wait_for(
            fallback.run_inference(
                LLMContext([{"role": "user", "content": "Ada"}]),
                system_instruction="Extract name",
            ),
            1,
        )
        == '{"name":"Ada"}'
    )
    assert len(usage.llm) == 2
    assert sum(value["prompt_tokens"] for value in usage.llm.values()) == 6000
    assert len(usage.prompt_cache) == 2
    specs = [call.args[0] for call in registry.prepare_inference.await_args_list]
    assert specs[0].key != specs[1].key
    assert all(item.organization_id == 7 for item in specs)
