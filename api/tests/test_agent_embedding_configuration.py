"""Knowledge-base callbacks remain bound to the originating agent visit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.ai_model_configuration import dograh_embeddings_base_url
from api.services.configuration.registry import (
    DograhEmbeddingsConfiguration,
    OpenAIEmbeddingsConfiguration,
)
from api.services.workflow import pipecat_engine


def agent(configuration):
    return SimpleNamespace(
        user_config=configuration,
        llm=Mock(),
        bind_tool=lambda engine, callback: callback,
    )


def engine_stub():
    return SimpleNamespace(
        active_agent=None,
        _get_organization_id=AsyncMock(return_value=7),
        _get_otel_context=lambda: None,
        _call_context_vars={"mps_correlation_id": "authorized-run"},
        _embeddings_api_key="legacy-key",
        _embeddings_model="legacy-model",
        _embeddings_base_url=None,
        _embeddings_provider="openai",
        _embeddings_endpoint=None,
        _embeddings_api_version=None,
    )


@pytest.mark.asyncio
async def test_registered_kb_callback_uses_captured_visit_not_active_agent(monkeypatch):
    retrieve = AsyncMock(return_value={"chunks": []})
    monkeypatch.setattr(pipecat_engine, "retrieve_from_knowledge_base", retrieve)
    engine = engine_stub()
    destination = agent(
        EffectiveAIModelConfiguration(
            embeddings=DograhEmbeddingsConfiguration(api_key="selected-key")
        )
    )
    await pipecat_engine.PipecatEngine._register_knowledge_base_function(
        engine, ["destination-document"], agent=destination
    )
    engine.active_agent = agent(
        EffectiveAIModelConfiguration(
            embeddings=OpenAIEmbeddingsConfiguration(api_key="new-active-key")
        )
    )
    callback = destination.llm.register_function.call_args.args[1]
    result = AsyncMock()
    await callback(
        SimpleNamespace(arguments={"query": "Question"}, result_callback=result)
    )
    arguments = retrieve.await_args.kwargs
    assert arguments["organization_id"] == 7
    assert arguments["document_uuids"] == ["destination-document"]
    assert arguments["embeddings_api_key"] == "selected-key"
    assert arguments["embeddings_model"] == "dograh_embedding_v1"
    assert arguments["embeddings_base_url"] == dograh_embeddings_base_url()
    assert arguments["correlation_id"] == "authorized-run"
    result.assert_awaited_once_with({"chunks": []})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configuration, expected_key",
    [(None, "legacy-key"), (EffectiveAIModelConfiguration(), None)],
)
async def test_only_missing_legacy_visit_configuration_inherits_engine_embeddings(
    monkeypatch, configuration, expected_key
):
    retrieve = AsyncMock(return_value={"chunks": []})
    monkeypatch.setattr(pipecat_engine, "retrieve_from_knowledge_base", retrieve)
    engine = engine_stub()
    destination = agent(configuration)
    await pipecat_engine.PipecatEngine._register_knowledge_base_function(
        engine, ["document"], agent=destination
    )
    callback = destination.llm.register_function.call_args.args[1]
    await callback(
        SimpleNamespace(arguments={"query": "Question"}, result_callback=AsyncMock())
    )
    assert retrieve.await_args.kwargs["embeddings_api_key"] == expected_key
