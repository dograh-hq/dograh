from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
@pytest.mark.parametrize("provider", ["google", "google_vertex", "openai"])
@pytest.mark.parametrize("mode", ["inline", "call_context"])
async def test_mcp_preview_only_reports_prewarming_for_eligible_caching(
    monkeypatch, provider, mode
):
    from copy import deepcopy

    from api.enums import ToolCategory
    from api.schemas.workflow_configurations import PromptVariablesConfiguration
    from api.services.workflow import prompt_preview
    from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION

    effective = Mock(
        llm=SimpleNamespace(provider=provider, model="test-model"), is_realtime=False
    )
    monkeypatch.setattr(
        prompt_preview,
        "resolve_model_configuration",
        AsyncMock(return_value=SimpleNamespace(effective=effective)),
    )
    monkeypatch.setattr(
        prompt_preview.db_client, "has_active_recordings", AsyncMock(return_value=False)
    )
    tool_id = "00000000-0000-0000-0000-000000000001"
    lookup = AsyncMock(
        side_effect=lambda ids, organization_id: (
            [
                SimpleNamespace(
                    tool_uuid=tool_id, name="Test MCP", category=ToolCategory.MCP.value
                )
            ]
            if tool_id in ids
            else []
        )
    )
    monkeypatch.setattr(prompt_preview.db_client, "get_tools_by_uuids", lookup)
    service = Mock(
        cleanup=AsyncMock(), prompt_cache_spec_for_node=Mock(return_value=None)
    )
    factory = Mock(return_value=service)
    monkeypatch.setattr(
        "api.services.pipecat.service_factory.create_llm_service", factory
    )
    graph = deepcopy(DEFAULT_WORKFLOW_DEFINITION)
    graph["nodes"][0]["data"]["tool_uuids"] = [tool_id]
    result = await prompt_preview.build_prompt_preview(
        definition=SimpleNamespace(
            workflow_id=12,
            workflow_json=graph,
            workflow_configurations={},
            template_context_variables={},
        ),
        organization_id=7,
        config=PromptVariablesConfiguration(mode=mode),
    )
    eligible = provider in {"google", "google_vertex"} and mode == "call_context"
    assert result.nodes[0].cache_status == ("requires_live_tools" if eligible else None)
    assert any("incomplete tool/token previews" in notice for notice in result.notices)
    assert any("prewarmed" in notice for notice in result.notices) == eligible
    assert factory.called == eligible
    assert service.cleanup.await_count == int(eligible)
    assert all(call.args[1] == 7 for call in lookup.call_args_list)


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
