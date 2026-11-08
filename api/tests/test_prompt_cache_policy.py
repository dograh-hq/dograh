"""The layout opt-in controls cache policy across runtime, publish, and replay."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.prompt_preview import PromptReplayRequest
from api.schemas.workflow_configurations import PromptVariablesConfiguration
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.final_extraction import _close_llm
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.prompt_preview import prewarm_published_prompts
from api.services.workflow.prompt_variables import CallContextMessage
from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "google_vertex", "openai"])
@pytest.mark.parametrize("mode", ["inline", "call_context"])
@pytest.mark.parametrize("disabled", [False, True])
async def test_node_setup_automatically_selects_cache_policy(
    monkeypatch, three_node_workflow, provider, mode, disabled
):
    monkeypatch.setenv("DOGRAH_PROMPT_CACHE_DISABLED", "1" if disabled else "0")
    registry = SimpleNamespace(prewarm=Mock())
    monkeypatch.setattr(
        "api.services.pipecat.gemini_prompt_cache.get_prompt_cache_registry",
        lambda: registry,
    )
    llm = create_llm_service_from_provider(
        provider=provider,
        model="gemini-3-flash-preview" if provider != "openai" else "gpt-4.1",
        api_key="test-key",
        project_id="test-project",
        location="global",
        credentials='{"key": "test"}',
    )
    engine = PipecatEngine(
        llm=llm,
        context=LLMContext(),
        workflow=three_node_workflow,
        call_context_vars={},
        workflow_run_id=1,
        prompt_variables=PromptVariablesConfiguration(mode=mode),
    )
    engine.active_agent.workflow_id = 12
    engine._get_organization_id = AsyncMock(return_value=7)
    try:
        await engine._setup_llm_context(
            three_node_workflow.nodes[three_node_workflow.start_node_id]
        )
        await asyncio.gather(*list(engine._prompt_prewarm_tasks))
        if mode == "call_context" and provider != "openai" and not disabled:
            specs = [call.args[0] for call in registry.prewarm.call_args_list]
            assert {spec.node_id for spec in specs} >= {
                three_node_workflow.start_node_id,
                *three_node_workflow.nodes[three_node_workflow.start_node_id].out,
            }
            assert all(
                spec.organization_id == 7 and spec.workflow_id == 12 for spec in specs
            )
        else:
            registry.prewarm.assert_not_called()
    finally:
        await _close_llm(llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["inline", "call_context"])
async def test_publish_uses_layout_without_a_separate_cache_flag(monkeypatch, mode):
    from api.services.workflow import prompt_preview

    monkeypatch.delenv("DOGRAH_PROMPT_CACHE_DISABLED", raising=False)
    llm = SimpleNamespace(
        configure_prompt_cache=Mock(), prewarm_prompt_cache=Mock(), cleanup=AsyncMock()
    )
    create = Mock(return_value=llm)
    monkeypatch.setattr(
        "api.services.pipecat.service_factory.create_llm_service", create
    )
    monkeypatch.setattr(
        prompt_preview,
        "resolve_model_configuration",
        AsyncMock(
            return_value=SimpleNamespace(effective=SimpleNamespace(is_realtime=False))
        ),
    )
    monkeypatch.setattr(
        prompt_preview.db_client, "has_active_recordings", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "api.services.pipecat.prompt_cache.get_prompt_cache_registry",
        lambda: SimpleNamespace(tasks={}),
    )
    definition = SimpleNamespace(
        workflow_json=DEFAULT_WORKFLOW_DEFINITION,
        workflow_configurations={"prompt_variables": {"mode": mode}},
    )
    await prewarm_published_prompts(
        organization_id=7, workflow_id=12, definition=definition
    )
    if mode == "call_context":
        assert llm.prewarm_prompt_cache.call_count == 2
        assert all(
            call.kwargs["enabled"] for call in llm.configure_prompt_cache.call_args_list
        )
        llm.cleanup.assert_awaited_once()
    else:
        create.assert_not_called()


@pytest.mark.asyncio
async def test_replay_applies_automatic_policy_only_to_optimized_samples(monkeypatch):
    from api.services.workflow import prompt_replay

    run = SimpleNamespace(
        id=1,
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
        initial_context={"name": "Ada"},
        logs={
            "realtime_feedback_events": [
                {"type": "rtf-user-transcription", "payload": {"text": "Hello"}},
                {"type": "rtf-bot-text", "payload": {"text": "Hi"}},
            ]
        },
    )
    monkeypatch.setattr(
        prompt_replay.db_client, "get_prompt_replay_runs", AsyncMock(return_value=[run])
    )
    monkeypatch.setattr(
        prompt_replay.db_client, "has_active_recordings", AsyncMock(return_value=False)
    )
    effective = SimpleNamespace(
        is_realtime=False, model_copy=Mock(return_value=object())
    )
    monkeypatch.setattr(
        prompt_replay,
        "resolve_model_configuration",
        AsyncMock(return_value=SimpleNamespace(effective=effective)),
    )
    monkeypatch.setattr(
        prompt_replay, "preview_node_functions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "api.services.pipecat.prompt_cache.get_prompt_cache_registry",
        lambda: SimpleNamespace(tasks={}),
    )
    services = []

    def create(*args, **kwargs):
        llm = SimpleNamespace(
            configure_prompt_cache=Mock(),
            prewarm_prompt_cache=Mock(),
            cleanup=AsyncMock(),
            _update_settings=AsyncMock(),
            run_inference=AsyncMock(
                return_value='{"same_move":true,"facts_consistent":true}'
            ),
        )
        services.append(llm)
        return llm

    async def infer(llm, context):
        optimized = any(
            isinstance(message, CallContextMessage) for message in context.messages
        )
        assert llm.configure_prompt_cache.called == optimized
        assert llm.prewarm_prompt_cache.called == optimized
        return {
            "text": "Hi",
            "tool_calls": [],
            "usage": {},
            "error": None,
            "ttft_seconds": 0.1,
        }

    monkeypatch.setattr(prompt_replay, "create_llm_service", create)
    monkeypatch.setattr(prompt_replay, "run_replay_inference", infer)
    result = await prompt_replay.compare_recent_prompts(
        organization_id=7,
        workflow_id=12,
        definition=SimpleNamespace(
            workflow_json=DEFAULT_WORKFLOW_DEFINITION, workflow_configurations={}
        ),
        config=PromptVariablesConfiguration(),
    )
    assert result["valid_turns"] == 1
    assert sum(service.prewarm_prompt_cache.called for service in services) == 2
    assert "explicit" not in PromptReplayRequest.model_json_schema()["properties"]
