from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.workflow import router
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.auth.depends import get_user
from api.services.configuration import ai_model_configuration as amc
from api.services.configuration import provider_profiles as profiles
from api.services.configuration.call_model_overrides import (
    CallOverrideError,
    validate_workflow_profile_selection,
)

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
ELEVEN = {"provider": "elevenlabs", "api_key": "el-eu-2222", "voice": "Rachel"}
SELECTION = {"llm": {"profile": "openai-cheap"}, "tts": {"profile": "eleven-eu"}}


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value, **_kwargs):
        self.rows[(organization_id, key)] = value

    async def upsert_configuration_with_lock(
        self, organization_id, key, mutate, **_kwargs
    ):
        current = self.rows.get((organization_id, key))
        new_value = mutate(current)
        self.rows[(organization_id, key)] = new_value
        return SimpleNamespace(value=new_value)


@pytest.fixture
async def saved():
    store = FakeStore()
    base = EffectiveAIModelConfiguration.model_validate(BASE)
    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
        patch.object(
            amc,
            "get_resolved_ai_model_configuration",
            new=AsyncMock(return_value=SimpleNamespace(effective=base)),
        ),
    ):
        await profiles.create_profile(
            ORG_ID, name="openai-cheap", service="llm", config=CHEAP
        )
        await profiles.create_profile(
            ORG_ID, name="eleven-eu", service="tts", config=ELEVEN
        )
        yield store


# --------------------------------------------------------------- resolver


@pytest.mark.asyncio
async def test_workflow_selection_replaces_only_the_selected_services(saved):
    effective = await amc.get_effective_ai_model_configuration_for_workflow(
        organization_id=ORG_ID,
        workflow_configurations={amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: SELECTION},
    )

    assert effective.llm.model == "gpt-4.1-nano"
    assert effective.llm.api_key == "sk-cheap-1111"
    assert effective.tts.provider == "elevenlabs"
    assert effective.stt.model == "nova-3"  # not selected, stays the org default


@pytest.mark.asyncio
async def test_workflow_without_selection_is_unchanged(saved):
    effective = await amc.get_effective_ai_model_configuration_for_workflow(
        organization_id=ORG_ID, workflow_configurations={}
    )
    assert effective.llm.model == "gpt-4.1"


@pytest.mark.asyncio
async def test_selection_layers_on_top_of_a_workflow_model_override(saved):
    v2_override = {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "pipeline",
            "pipeline": {
                "llm": {
                    "provider": "openai",
                    "api_key": "sk-wf",
                    "model": "gpt-4.1-mini",
                },
                "tts": {
                    "provider": "deepgram",
                    "api_key": "dg-wf",
                    "voice": "wf-voice",
                },
                "stt": {"provider": "deepgram", "api_key": "dg-wf", "model": "nova-2"},
            },
        },
    }
    effective = await amc.get_effective_ai_model_configuration_for_workflow(
        organization_id=ORG_ID,
        workflow_configurations={
            amc.WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: v2_override,
            amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: {
                "llm": {"profile": "openai-cheap"}
            },
        },
    )

    assert effective.llm.model == "gpt-4.1-nano"  # selection wins
    assert effective.stt.model == "nova-2"  # workflow override otherwise


@pytest.mark.asyncio
async def test_deleted_profile_fails_loudly_at_run_time(saved):
    await profiles.delete_profile(ORG_ID, service="llm", name="openai-cheap")
    with pytest.raises(CallOverrideError, match="openai-cheap"):
        await amc.get_effective_ai_model_configuration_for_workflow(
            organization_id=ORG_ID,
            workflow_configurations={
                amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: SELECTION
            },
        )


# ------------------------------------------------------------- validation


@pytest.mark.asyncio
async def test_validate_returns_normalised_selection_without_secrets(saved):
    stored = await validate_workflow_profile_selection(
        ORG_ID, {amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: SELECTION}
    )
    assert stored == SELECTION
    assert "api_key" not in str(stored)


@pytest.mark.asyncio
async def test_validate_empty_selection_is_none(saved):
    assert (
        await validate_workflow_profile_selection(
            ORG_ID, {amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: {}}
        )
        is None
    )
    assert await validate_workflow_profile_selection(ORG_ID, {}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection, message",
    [
        ({"llm": {"profile": "missing"}}, "No saved llm profile"),
        ({"llm": {"profile": "openai-cheap", "api_key": "x"}}, "cannot be overridden"),
        ({"embeddings": {"profile": "x"}}, "embeddings"),
    ],
)
async def test_validate_rejects_bad_selections(saved, selection, message):
    with pytest.raises(CallOverrideError, match=message):
        await validate_workflow_profile_selection(
            ORG_ID, {amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: selection}
        )


@pytest.mark.asyncio
async def test_validate_rejects_selection_that_leaves_no_runnable_config(saved):
    incomplete = EffectiveAIModelConfiguration.model_validate({"llm": BASE["llm"]})
    with (
        patch.object(
            amc,
            "get_resolved_ai_model_configuration",
            new=AsyncMock(return_value=SimpleNamespace(effective=incomplete)),
        ),
        pytest.raises(CallOverrideError, match="tts, stt"),
    ):
        await validate_workflow_profile_selection(
            ORG_ID,
            {
                amc.WORKFLOW_MODEL_PROFILE_SELECTION_KEY: {
                    "llm": {"profile": "openai-cheap"}
                }
            },
        )


# ------------------------------------------------------------------ route


@pytest.fixture
def passthrough_pbx_policy():
    """External-PBX policy reads the DB; irrelevant to these tests."""
    with patch(
        "api.routes.workflow.apply_external_pbx_mapping_policy",
        new=AsyncMock(side_effect=lambda configs, **_kwargs: configs),
    ):
        yield


def _client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1, provider_id="provider-1", selected_organization_id=ORG_ID
    )
    return TestClient(app)


def test_update_workflow_rejects_unknown_saved_provider_before_db_write(
    saved, passthrough_pbx_policy
):
    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.update_workflow = AsyncMock()
        response = _client().put(
            "/workflow/33",
            json={
                "workflow_configurations": {
                    "model_profile_selection": {"llm": {"profile": "missing"}}
                }
            },
        )

    assert response.status_code == 422
    assert "No saved llm profile named 'missing'" in response.json()["detail"]
    mock_db.update_workflow.assert_not_awaited()


def test_update_workflow_stores_selection_by_name_and_drops_empty_ones(
    saved, passthrough_pbx_policy
):
    with patch("api.routes.workflow.db_client") as mock_db:
        # Stop after the value reaches the DB layer; the response is not under test.
        mock_db.update_workflow = AsyncMock(side_effect=RuntimeError("stop here"))

        _client().put(
            "/workflow/33",
            json={"workflow_configurations": {"model_profile_selection": SELECTION}},
        )
        saved_configs = mock_db.update_workflow.await_args.kwargs[
            "workflow_configurations"
        ]
        assert saved_configs["model_profile_selection"] == SELECTION
        assert "sk-cheap-1111" not in str(saved_configs)

        mock_db.update_workflow.reset_mock()
        _client().put(
            "/workflow/33",
            json={"workflow_configurations": {"model_profile_selection": {}}},
        )
        cleared = mock_db.update_workflow.await_args.kwargs["workflow_configurations"]
        assert "model_profile_selection" not in cleared
