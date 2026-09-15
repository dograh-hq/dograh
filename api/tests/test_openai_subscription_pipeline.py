from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.pipecat import run_pipeline
from api.services.pipecat.audio_config import create_audio_config
from api.services.workflow.disposition_extraction import DispositionExtractionService
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION


class PipelineServicesConfigured(Exception):
    pass


def subscription_config(*, legacy_llm=None):
    return EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_live_subscription",
            },
            "llm": legacy_llm,
        }
    )


async def configure_pipeline(monkeypatch, config, *, extraction=True):
    definition = deepcopy(DEFAULT_WORKFLOW_DEFINITION)
    if extraction:
        definition["nodes"][0]["data"].update(
            extraction_enabled=True,
            extraction_variables=[{"name": "reference", "type": "string"}],
        )
    run_configs = {
        "call_dispositions": (
            [{"code": "confirmed", "description": "Caller confirmed the reference"}]
            if extraction
            else []
        )
    }
    workflow_run = SimpleNamespace(
        workflow_id=10,
        is_completed=False,
        initial_context={},
        definition=SimpleNamespace(
            workflow_json=definition, workflow_configurations=run_configs
        ),
    )
    monkeypatch.setattr(
        run_pipeline.db_client,
        "get_workflow",
        AsyncMock(
            return_value=SimpleNamespace(organization_id=42, workflow_configurations={})
        ),
    )
    update_run = AsyncMock()
    monkeypatch.setattr(run_pipeline.db_client, "update_workflow_run", update_run)
    monkeypatch.setattr(
        run_pipeline.db_client, "has_active_recordings", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(run_pipeline, "get_ws_sender", lambda _: None)
    subscription_inference = SimpleNamespace(
        run_inference=AsyncMock(), model_name="gpt-5.6-luna"
    )
    voice = SimpleNamespace(
        inference_llm=subscription_inference, backend_model="gpt-5.6-luna"
    )
    realtime_factory = Mock(return_value=voice)
    api_inference = SimpleNamespace(run_inference=AsyncMock())
    extraction_inference = SimpleNamespace(run_inference=AsyncMock())
    llm_factory = Mock(
        side_effect=lambda *a, **kw: (
            extraction_inference
            if kw.get("usage_context") == "variable_extraction"
            else api_inference
        )
    )
    stt_factory = Mock()
    tts_factory = Mock()
    monkeypatch.setattr(run_pipeline, "create_realtime_llm_service", realtime_factory)
    monkeypatch.setattr(run_pipeline, "create_llm_service", llm_factory)
    monkeypatch.setattr(run_pipeline, "create_stt_service", stt_factory)
    monkeypatch.setattr(run_pipeline, "create_tts_service", tts_factory)
    engine_factory = Mock(wraps=PipecatEngine)
    engines = []

    def capture_engine(**kwargs):
        engine = engine_factory(**kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(run_pipeline, "PipecatEngine", capture_engine)
    # Stop after real config/graph/engine construction, before media setup.
    monkeypatch.setattr(
        run_pipeline,
        "create_pipeline_components",
        Mock(side_effect=PipelineServicesConfigured),
    )
    with pytest.raises(PipelineServicesConfigured):
        await run_pipeline._run_pipeline_impl(
            transport=object(),
            workflow_id=10,
            workflow_run_id=11,
            user_id=12,
            organization_id=42,
            audio_config=create_audio_config("smallwebrtc"),
            workflow_run=workflow_run,
            resolved_user_config=config,
        )
    assert len(engines) == 1
    return SimpleNamespace(
        engine=engines[0],
        config=config,
        runtime=update_run.await_args.kwargs["initial_context"][
            "runtime_configuration"
        ],
        subscription_inference=subscription_inference,
        voice=voice,
        api_inference=api_inference,
        extraction_inference=extraction_inference,
        llm_factory=llm_factory,
        realtime_factory=realtime_factory,
        stt_factory=stt_factory,
        tts_factory=tts_factory,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("extraction", [False, True])
@pytest.mark.parametrize(
    "legacy_llm",
    [None, {"provider": "dograh", "model": "default", "api_key": "unused"}],
)
async def test_subscription_pipeline_uses_subscription_reasoning_without_api_factory(
    monkeypatch, extraction, legacy_llm
):
    wired = await configure_pipeline(
        monkeypatch, subscription_config(legacy_llm=legacy_llm), extraction=extraction
    )
    assert wired.config.realtime.backend_model == "gpt-5.6-luna"
    assert wired.engine.llm is wired.voice
    assert wired.engine.inference_llm is wired.subscription_inference
    assert wired.engine.variable_extraction_llm is wired.subscription_inference
    assert wired.runtime == {
        "realtime_provider": "openai_live_subscription",
        "realtime_model": "gpt-live-1-codex",
        "llm_provider": "openai_live_subscription",
        "llm_model": "gpt-5.6-luna",
    }
    wired.llm_factory.assert_not_called()
    wired.stt_factory.assert_not_called()
    wired.tts_factory.assert_not_called()
    assert wired.realtime_factory.call_args.kwargs["organization_id"] == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.parametrize("provider", ["openai", "dograh"])
async def test_existing_pipeline_and_realtime_keep_configured_api_services(
    monkeypatch, realtime, provider
):
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": realtime,
            "llm": {"provider": provider, "api_key": "synthetic-key"},
            "realtime": {"provider": "openai_realtime", "api_key": "synthetic-key"},
            "stt": {"provider": "deepgram", "api_key": "synthetic-key"},
            "tts": {"provider": "openai", "api_key": "synthetic-key"},
        }
    )
    wired = await configure_pipeline(monkeypatch, config)
    assert wired.engine.inference_llm is wired.api_inference
    assert wired.engine.variable_extraction_llm is (
        wired.extraction_inference if provider == "dograh" else wired.api_inference
    )
    assert wired.runtime["llm_provider"] == provider
    assert wired.runtime["llm_model"] == config.llm.model
    assert wired.llm_factory.call_count == (2 if provider == "dograh" else 1)
    assert wired.realtime_factory.call_count == int(realtime)
    assert wired.stt_factory.call_count == int(not realtime)
    assert wired.tts_factory.call_count == int(not realtime)
    if provider == "dograh":
        assert (
            wired.llm_factory.call_args.kwargs["usage_context"] == "variable_extraction"
        )


@pytest.mark.asyncio
async def test_subscription_pipeline_extraction_consumers_execute_on_subscription(
    monkeypatch,
):
    wired = await configure_pipeline(monkeypatch, subscription_config())
    wired.engine.set_context(
        LLMContext([{"role": "user", "content": "I confirm ABC."}])
    )
    wired.subscription_inference.run_inference.side_effect = [
        '{"reference": "ABC"}',
        '{"call_disposition": "confirmed"}',
    ]
    extracted = await VariableExtractionManager(wired.engine)._perform_extraction(
        [ExtractionVariableDTO(name="reference", type="string")], None
    )
    assert extracted == {"reference": "ABC"}
    disposition = DispositionExtractionService(
        llm=wired.engine.variable_extraction_llm,
        context=wired.engine.context,
        options=wired.engine._call_dispositions,
        template_context={},
    )
    assert await disposition.extract() == "confirmed"
    assert wired.subscription_inference.run_inference.await_count == 2
    wired.llm_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_workflow_llm", [True, False])
async def test_answer_supervisor_reuses_subscription_or_explicit_override(
    monkeypatch, use_workflow_llm
):
    subscription = SimpleNamespace(run_inference=AsyncMock(return_value="SCREENER"))
    override = SimpleNamespace(run_inference=AsyncMock(return_value="SCREENER"))
    workflow_factory = Mock()
    override_factory = Mock(return_value=override)
    monkeypatch.setattr(run_pipeline, "create_llm_service", workflow_factory)
    monkeypatch.setattr(
        run_pipeline, "create_llm_service_from_provider", override_factory
    )
    supervisor = run_pipeline._create_answer_supervisor(
        {
            "enabled": True,
            "use_workflow_llm": use_workflow_llm,
            "provider": "openai",
            "model": "gpt-4.1",
            "api_key": "explicit-override-key",
        },
        call_direction="outbound",
        is_realtime=False,
        start_node=None,
        context=LLMContext(),
        user_config=None,
        correlation_id=None,
        workflow_inference_llm=subscription,
    )
    try:
        await supervisor._classify_turn("An ambiguous machine answer", 0)
        assert (await supervisor.wait_for_verdict()).action == "screen_then_rearm"
        selected = subscription if use_workflow_llm else override
        selected.run_inference.assert_awaited_once()
        workflow_factory.assert_not_called()
        if use_workflow_llm:
            override_factory.assert_not_called()
        else:
            subscription.run_inference.assert_not_awaited()
            override_factory.assert_called_once_with(
                provider="openai",
                model="gpt-4.1",
                api_key="explicit-override-key",
                usage_context="voicemail_detection",
            )
    finally:
        await supervisor.close()
