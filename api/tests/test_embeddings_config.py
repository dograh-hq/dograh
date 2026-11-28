"""Tests for the OpenAI embeddings configuration ``base_url`` field (#616)."""

from unittest.mock import patch

import pytest

from api.services.configuration.registry import OpenAIEmbeddingsConfiguration
from api.services.gen_ai.embedding.factory import build_embedding_service

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def test_embeddings_config_default_base_url():
    config = OpenAIEmbeddingsConfiguration(api_key="sk-test")

    assert config.base_url == _DEFAULT_BASE_URL


def test_embeddings_config_custom_base_url():
    config = OpenAIEmbeddingsConfiguration(
        api_key="sk-test",
        base_url="https://api.x.ai/v1",
    )

    assert config.base_url == "https://api.x.ai/v1"


@pytest.mark.asyncio
async def test_build_embedding_service_forwards_openai_base_url():
    config = OpenAIEmbeddingsConfiguration(
        api_key="sk-test",
        base_url="https://api.x.ai/v1",
    )

    with patch(
        "api.services.gen_ai.embedding.factory.OpenAIEmbeddingService"
    ) as openai_service:
        await build_embedding_service(
            db_client=object(),
            provider="openai",
            api_key="sk-test",
            model="text-embedding-3-small",
            base_url=config.base_url,
        )

    openai_service.assert_called_once()
    assert openai_service.call_args.kwargs["base_url"] == "https://api.x.ai/v1"
