"""Tests for QA LLM config resolution (#527).

Ensures a configured OpenAI-compatible ``base_url`` is forwarded to the QA
analysis LLM — both when QA reuses the workflow LLM and when the QA node has
its own LLM — so QA reaches the same endpoint the conversation path uses
instead of silently falling back to the provider default and 401'ing.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.qa.llm_config import (
    resolve_llm_config,
    resolve_user_llm_config,
)

_CONFIG_FN = (
    "api.services.configuration.ai_model_configuration"
    ".get_effective_ai_model_configuration_for_workflow"
)


def _qa(**overrides):
    data = dict(
        qa_use_workflow_llm=False,
        qa_provider="openai",
        qa_endpoint=None,
        qa_model="m",
        qa_api_key="k",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _workflow_run(llm_cfg):
    config = SimpleNamespace(model_dump=lambda **kw: {"llm": llm_cfg})
    run = SimpleNamespace(
        workflow=SimpleNamespace(organization_id=1, workflow_configurations={}),
        definition=None,
    )
    return run, config


# --- QA node's own LLM (qa_use_workflow_llm=False) ---


@pytest.mark.asyncio
async def test_qa_own_llm_forwards_base_url_for_non_azure():
    qa = _qa(
        qa_provider="openai",
        qa_endpoint="https://api.x.ai/v1",
        qa_model="grok-3-fast",
        qa_api_key="xai-key",
    )
    provider, model, api_key, kwargs = await resolve_llm_config(qa, None)
    assert provider == "openai"
    assert api_key == "xai-key"
    assert kwargs == {"base_url": "https://api.x.ai/v1"}


@pytest.mark.asyncio
async def test_qa_own_llm_azure_uses_endpoint_not_base_url():
    qa = _qa(qa_provider="azure", qa_endpoint="https://x.openai.azure.com")
    _, _, _, kwargs = await resolve_llm_config(qa, None)
    assert kwargs == {"endpoint": "https://x.openai.azure.com"}


@pytest.mark.asyncio
async def test_qa_own_llm_without_endpoint_forwards_nothing():
    qa = _qa(qa_provider="openai", qa_endpoint=None)
    _, _, _, kwargs = await resolve_llm_config(qa, None)
    assert kwargs == {}


# --- QA reuses the workflow/org LLM (qa_use_workflow_llm=True) ---


@pytest.mark.asyncio
async def test_workflow_llm_forwards_base_url_for_openai():
    run, config = _workflow_run(
        {
            "provider": "openai",
            "base_url": "https://api.x.ai/v1",
            "api_key": "xai-key",
            "model": "grok-3-fast",
        }
    )
    with patch(_CONFIG_FN, AsyncMock(return_value=config)):
        provider, model, api_key, kwargs = await resolve_user_llm_config(run)
    assert provider == "openai"
    assert kwargs == {"base_url": "https://api.x.ai/v1"}


@pytest.mark.asyncio
async def test_workflow_llm_azure_uses_endpoint_not_base_url():
    run, config = _workflow_run(
        {
            "provider": "azure",
            "endpoint": "https://x.openai.azure.com",
            "api_key": "k",
            "model": "gpt-4.1",
        }
    )
    with patch(_CONFIG_FN, AsyncMock(return_value=config)):
        _, _, _, kwargs = await resolve_user_llm_config(run)
    assert kwargs == {"endpoint": "https://x.openai.azure.com"}


@pytest.mark.asyncio
async def test_workflow_llm_without_base_url_forwards_nothing():
    run, config = _workflow_run(
        {"provider": "openai", "api_key": "k", "model": "gpt-4.1"}
    )
    with patch(_CONFIG_FN, AsyncMock(return_value=config)):
        _, _, _, kwargs = await resolve_user_llm_config(run)
    assert kwargs == {}
