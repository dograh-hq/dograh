from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.routes import workflow as routes
from api.schemas.prompt_preview import PromptPreviewRequest, PromptReplayRequest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,payload",
    [
        (routes.preview_workflow_prompts, PromptPreviewRequest()),
        (routes.replay_workflow_prompts, PromptReplayRequest()),
    ],
)
async def test_preview_and_replay_scope_workflow_before_reading_content(
    monkeypatch, handler, payload
):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.db_client, "get_workflow", lookup)
    with pytest.raises(HTTPException) as error:
        await handler(12, payload, SimpleNamespace(selected_organization_id=7))
    assert error.value.status_code == 404
    lookup.assert_awaited_once_with(12, organization_id=7)


@pytest.mark.asyncio
async def test_preview_rejects_sample_run_from_another_workflow(monkeypatch):
    monkeypatch.setattr(
        routes.db_client,
        "get_workflow",
        AsyncMock(return_value=SimpleNamespace(released_definition=object())),
    )
    monkeypatch.setattr(
        routes.db_client, "get_draft_version", AsyncMock(return_value=None)
    )
    lookup = AsyncMock(return_value=SimpleNamespace(workflow_id=99))
    monkeypatch.setattr(routes.db_client, "get_workflow_run", lookup)
    with pytest.raises(HTTPException) as error:
        await routes.preview_workflow_prompts(
            12,
            PromptPreviewRequest(sample_run_id=3),
            SimpleNamespace(selected_organization_id=7),
        )
    assert error.value.status_code == 404
    lookup.assert_awaited_once_with(3, organization_id=7)


@pytest.mark.asyncio
async def test_preview_uses_runtime_google_schema_and_cache_identity(monkeypatch):
    from unittest.mock import Mock

    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.schemas.workflow_configurations import PromptVariablesConfiguration
    from api.services.workflow import prompt_preview
    from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION

    config = EffectiveAIModelConfiguration.model_validate(
        {
            "llm": {
                "provider": "google",
                "api_key": "test-key",
                "model": "gemini-3-flash-preview",
            }
        }
    )
    monkeypatch.setattr(
        prompt_preview,
        "resolve_model_configuration",
        AsyncMock(return_value=SimpleNamespace(effective=config)),
    )
    monkeypatch.setattr(
        prompt_preview.db_client, "has_active_recordings", AsyncMock(return_value=False)
    )
    registry = SimpleNamespace(
        status=AsyncMock(return_value=("ready", 6000)), prewarm=Mock()
    )
    monkeypatch.setattr(
        "api.services.pipecat.prompt_cache.get_prompt_cache_registry", lambda: registry
    )
    definition = SimpleNamespace(
        workflow_id=12,
        workflow_json=DEFAULT_WORKFLOW_DEFINITION,
        workflow_configurations={},
        template_context_variables={},
    )
    result = await prompt_preview.build_prompt_preview(
        definition=definition,
        organization_id=7,
        config=PromptVariablesConfiguration(mode="call_context"),
    )
    assert result.explicit_cache_available
    assert result.nodes[0].cache_status == "ready"
    assert result.nodes[0].static_tokens_estimate == 6000
    assert set(result.nodes[0].tools[0]) == {"name", "description", "parameters"}
    assert result.nodes[0].tools[0]["name"]
    assert all(
        call.args[0].organization_id == 7 and call.args[0].workflow_id == 12
        for call in registry.status.call_args_list
    )
    registry.prewarm.assert_not_called()


@pytest.mark.asyncio
async def test_preview_only_translates_graph_errors_to_422(monkeypatch):
    from api.services.workflow.prompt_preview import PromptPreviewGraphError

    monkeypatch.setattr(
        routes.db_client,
        "get_workflow",
        AsyncMock(return_value=SimpleNamespace(released_definition=object())),
    )
    monkeypatch.setattr(
        routes.db_client, "get_draft_version", AsyncMock(return_value=None)
    )
    build = AsyncMock(side_effect=PromptPreviewGraphError("bad graph"))
    monkeypatch.setattr(routes, "build_prompt_preview", build)
    with pytest.raises(HTTPException) as error:
        await routes.preview_workflow_prompts(
            12, PromptPreviewRequest(), SimpleNamespace(selected_organization_id=7)
        )
    assert error.value.status_code == 422
    build.side_effect = ValueError("client configuration failed")
    with pytest.raises(ValueError, match="client configuration"):
        await routes.preview_workflow_prompts(
            12, PromptPreviewRequest(), SimpleNamespace(selected_organization_id=7)
        )
