from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import call_model_overrides as overrides_module
from api.services.configuration import provider_profiles as profiles
from api.services.pipecat import run_pipeline as run_pipeline_module
from api.services.workflow.initial_context import RUN_MODEL_OVERRIDES_CONTEXT_KEY

ORG_ID = 11
BASE = {
    "llm": {"provider": "openai", "api_key": "sk-default-0000", "model": "gpt-4.1"},
    "tts": {
        "provider": "deepgram",
        "api_key": "dg-default-0000",
        "voice": "aura-2-thalia-en",
    },
    "stt": {"provider": "deepgram", "api_key": "dg-default-0000", "model": "nova-3"},
}
CHEAP = {"provider": "openai", "api_key": "sk-cheap-1111", "model": "gpt-4.1-nano"}


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value, **_kwargs):
        self.rows[(organization_id, key)] = value


@pytest.mark.asyncio
async def test_telephony_pipeline_runs_with_the_calls_model_overrides():
    """The pipeline must receive the run's overridden config, not the default."""
    store = FakeStore()
    base_config = EffectiveAIModelConfiguration.model_validate(BASE)
    workflow = SimpleNamespace(
        id=33, user_id=99, organization_id=ORG_ID, workflow_configurations={}
    )
    workflow_run = SimpleNamespace(
        id=501,
        workflow_id=33,
        initial_context={
            RUN_MODEL_OVERRIDES_CONTEXT_KEY: {"llm": {"profile": "openai-cheap"}}
        },
        definition=SimpleNamespace(workflow_configurations={}),
    )
    transport_factory = AsyncMock(return_value=object())
    run_impl = AsyncMock()

    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
        patch.object(run_pipeline_module, "db_client") as mock_db,
        patch.object(run_pipeline_module, "telephony_registry") as registry,
        patch.object(run_pipeline_module, "create_audio_config"),
        patch.object(run_pipeline_module, "_run_pipeline_impl", new=run_impl),
        patch.object(
            overrides_module,
            "get_effective_ai_model_configuration_for_workflow",
            new=AsyncMock(return_value=base_config),
        ),
    ):
        await profiles.create_profile(
            ORG_ID, name="openai-cheap", service="llm", config=CHEAP
        )
        mock_db.get_workflow = AsyncMock(return_value=workflow)
        mock_db.get_workflow_run = AsyncMock(return_value=workflow_run)
        registry.get.return_value = SimpleNamespace(transport_factory=transport_factory)

        await run_pipeline_module.run_pipeline_telephony(
            websocket=object(),
            provider_name="twilio",
            workflow_id=33,
            workflow_run_id=501,
            organization_id=ORG_ID,
            call_id="CA1",
            transport_kwargs={},
        )

    resolved = run_impl.await_args.kwargs["resolved_user_config"]
    assert resolved.llm.model == "gpt-4.1-nano"
    assert resolved.llm.api_key == "sk-cheap-1111"
    # Untouched services keep the default configuration.
    assert resolved.tts.provider == "deepgram"
    assert base_config.llm.model == "gpt-4.1"
