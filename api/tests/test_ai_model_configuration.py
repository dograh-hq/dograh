"""Organization-level model configuration reads go through the catalog only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import ai_model_configuration, model_connections
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    ResolvedAIModelConfiguration,
    get_effective_ai_model_configuration_for_workflow,
    get_resolved_ai_model_configuration,
)
from api.services.configuration.registry import OpenRouterLLMConfiguration


@pytest.mark.asyncio
@pytest.mark.parametrize("organization_id", [None, 42])
async def test_without_a_default_the_configuration_is_empty(
    monkeypatch, organization_id
):
    monkeypatch.setattr(
        model_connections,
        "get_default_model_configuration",
        AsyncMock(return_value=None),
    )
    resolved = await get_resolved_ai_model_configuration(
        organization_id=organization_id
    )
    assert resolved.source == "empty"
    assert resolved.effective == EffectiveAIModelConfiguration()


@pytest.mark.asyncio
async def test_default_configuration_is_resolved_through_the_catalog(monkeypatch):
    row = SimpleNamespace(uuid="configuration-1")
    effective = EffectiveAIModelConfiguration(
        llm=OpenRouterLLMConfiguration(api_key="private-test-key", temperature=0.5)
    )
    resolve = AsyncMock(return_value=SimpleNamespace(effective=effective))
    monkeypatch.setattr(
        model_connections,
        "get_default_model_configuration",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(model_connections, "resolve_model_configuration", resolve)

    resolved = await get_resolved_ai_model_configuration(organization_id=42)

    assert (resolved.source, resolved.effective) == ("organization_v3", effective)
    resolve.assert_awaited_once_with(42, default_row=row)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_v2_override", [False, True])
async def test_retired_inline_workflow_model_keys_are_ignored(
    monkeypatch, use_v2_override
):
    """Rows converted by the catalog backfill keep their old payloads as audit data."""
    base = EffectiveAIModelConfiguration(
        llm=OpenRouterLLMConfiguration(api_key="private-test-key", temperature=1.5)
    )
    monkeypatch.setattr(
        ai_model_configuration,
        "get_resolved_ai_model_configuration",
        AsyncMock(
            return_value=ResolvedAIModelConfiguration(
                effective=base, source="organization_v3"
            )
        ),
    )
    if use_v2_override:
        config = {
            WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: {
                "mode": "dograh",
                "dograh": {"api_key": "private-test-key", "temperature": -1},
            }
        }
    else:
        config = {"model_overrides": {"llm": {"model": "anthropic/claude-sonnet-4"}}}

    effective = await get_effective_ai_model_configuration_for_workflow(
        organization_id=42,
        workflow_configurations=config,
    )

    assert effective is base
    assert base.llm.temperature == 1.5
