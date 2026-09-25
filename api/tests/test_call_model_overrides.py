from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_provider_profiles import CallModelOverrides
from api.services.configuration import call_model_overrides as overrides_module
from api.services.configuration import provider_profiles as profiles
from api.services.configuration.call_model_overrides import (
    CallOverrideError,
    apply_call_model_overrides,
    get_effective_ai_model_configuration_for_run,
    missing_services,
    validate_call_overrides,
)
from api.services.workflow.initial_context import RUN_MODEL_OVERRIDES_CONTEXT_KEY

ORG_ID = 11
DEFAULT_LLM = {"provider": "openai", "api_key": "sk-default-0000", "model": "gpt-4.1"}
DEFAULT_TTS = {
    "provider": "deepgram",
    "api_key": "dg-default-0000",
    "voice": "aura-2-thalia-en",
}
DEFAULT_STT = {"provider": "deepgram", "api_key": "dg-default-0000", "model": "nova-3"}
OPENAI_CHEAP = {
    "provider": "openai",
    "api_key": "sk-cheap-1111",
    "model": "gpt-4.1-nano",
}
ELEVEN_EU = {
    "provider": "elevenlabs",
    "api_key": "el-eu-2222",
    "voice": "Rachel",
    "speed": 1.0,
    "model": "eleven_flash_v2_5",
}
REALTIME = {
    "provider": "openai_realtime",
    "api_key": "sk-rt-3333",
    "model": "gpt-realtime-2",
}


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value, **_kwargs):
        self.rows[(organization_id, key)] = value


@pytest.fixture
async def saved_profiles():
    store = FakeStore()
    with (
        patch.object(profiles, "db_client", store),
        patch.object(
            profiles.UserConfigurationValidator,
            "validate_single_service",
            new=AsyncMock(return_value={"status": []}),
        ),
    ):
        await profiles.create_profile(
            ORG_ID, name="openai-cheap", service="llm", config=OPENAI_CHEAP
        )
        await profiles.create_profile(
            ORG_ID, name="eleven-eu", service="tts", config=ELEVEN_EU
        )
        await profiles.create_profile(
            ORG_ID, name="rt", service="realtime", config=REALTIME
        )
        yield store


@pytest.fixture
def base_config():
    return EffectiveAIModelConfiguration.model_validate(
        {"llm": DEFAULT_LLM, "tts": DEFAULT_TTS, "stt": DEFAULT_STT}
    )


# -------------------------------------------------------------- validation


@pytest.mark.asyncio
async def test_validate_returns_secret_free_dict(saved_profiles):
    stored = await validate_call_overrides(
        ORG_ID,
        CallModelOverrides.model_validate(
            {
                "llm": {"profile": "openai-cheap"},
                "tts": {"profile": "eleven-eu", "voice": "Bella"},
            }
        ),
    )

    assert stored == {
        "llm": {"profile": "openai-cheap"},
        "tts": {"profile": "eleven-eu", "voice": "Bella"},
    }
    assert "api_key" not in str(stored)


@pytest.mark.asyncio
async def test_validate_unknown_profile(saved_profiles):
    with pytest.raises(CallOverrideError, match="No saved llm profile named 'nope'"):
        await validate_call_overrides(
            ORG_ID, CallModelOverrides.model_validate({"llm": {"profile": "nope"}})
        )


@pytest.mark.asyncio
async def test_validate_profile_of_another_service_is_not_found(saved_profiles):
    with pytest.raises(CallOverrideError, match="No saved tts profile"):
        await validate_call_overrides(
            ORG_ID,
            CallModelOverrides.model_validate({"tts": {"profile": "openai-cheap"}}),
        )


@pytest.mark.asyncio
async def test_validate_profiles_are_scoped_to_the_organization(saved_profiles):
    with pytest.raises(CallOverrideError):
        await validate_call_overrides(
            ORG_ID + 1,
            CallModelOverrides.model_validate({"llm": {"profile": "openai-cheap"}}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["api_key", "provider", "base_url", "credentials"])
async def test_validate_rejects_secret_provider_and_url_overrides(
    saved_profiles, field
):
    with pytest.raises(CallOverrideError, match="cannot be overridden"):
        await validate_call_overrides(
            ORG_ID,
            CallModelOverrides.model_validate(
                {"llm": {"profile": "openai-cheap", field: "x"}}
            ),
        )


@pytest.mark.asyncio
async def test_validate_rejects_unknown_field(saved_profiles):
    with pytest.raises(CallOverrideError, match="unknown field"):
        await validate_call_overrides(
            ORG_ID,
            CallModelOverrides.model_validate(
                {"tts": {"profile": "eleven-eu", "voise": "Bella"}}
            ),
        )


@pytest.mark.asyncio
async def test_validate_rejects_wrong_value_type(saved_profiles):
    with pytest.raises(CallOverrideError, match="speed"):
        await validate_call_overrides(
            ORG_ID,
            CallModelOverrides.model_validate(
                {"tts": {"profile": "eleven-eu", "speed": "fast"}}
            ),
        )


def test_request_rejects_unknown_service_key():
    with pytest.raises(ValueError):
        CallModelOverrides.model_validate({"embeddings": {"profile": "x"}})


# ------------------------------------------------------------------- apply


@pytest.mark.asyncio
async def test_apply_replaces_only_the_named_services(saved_profiles, base_config):
    effective = await apply_call_model_overrides(
        base_config,
        ORG_ID,
        {
            "llm": {"profile": "openai-cheap"},
            "tts": {"profile": "eleven-eu", "voice": "Bella"},
        },
    )

    assert effective.llm.model == "gpt-4.1-nano"
    assert effective.llm.api_key == "sk-cheap-1111"
    assert effective.tts.provider == "elevenlabs"
    assert effective.tts.voice == "Bella"
    assert effective.tts.api_key == "el-eu-2222"
    # Untouched services and the input object are unchanged.
    assert effective.stt.model == "nova-3"
    assert base_config.llm.model == "gpt-4.1"
    assert base_config.tts.provider == "deepgram"


@pytest.mark.asyncio
async def test_apply_never_merges_default_endpoint_into_a_profile(saved_profiles):
    base = EffectiveAIModelConfiguration.model_validate(
        {
            "llm": {**DEFAULT_LLM, "base_url": "https://internal.example.com/v1"},
            "tts": DEFAULT_TTS,
            "stt": DEFAULT_STT,
        }
    )

    effective = await apply_call_model_overrides(
        base, ORG_ID, {"llm": {"profile": "openai-cheap"}}
    )

    assert effective.llm.api_key == "sk-cheap-1111"
    assert "internal.example.com" not in (effective.llm.base_url or "")


@pytest.mark.asyncio
async def test_realtime_profile_switches_to_speech_to_speech(
    saved_profiles, base_config
):
    effective = await apply_call_model_overrides(
        base_config, ORG_ID, {"realtime": {"profile": "rt"}}
    )

    assert effective.is_realtime is True
    assert effective.realtime.provider == "openai_realtime"
    assert effective.llm.model == "gpt-4.1"  # still needed for extraction


@pytest.mark.asyncio
async def test_explicit_is_realtime_false_wins(saved_profiles, base_config):
    effective = await apply_call_model_overrides(
        base_config, ORG_ID, {"realtime": {"profile": "rt"}, "is_realtime": False}
    )
    assert effective.is_realtime is False


@pytest.mark.asyncio
async def test_no_overrides_returns_the_base_unchanged(saved_profiles, base_config):
    assert await apply_call_model_overrides(base_config, ORG_ID, None) is base_config
    assert await apply_call_model_overrides(base_config, ORG_ID, {}) is base_config


def test_missing_services(base_config):
    assert missing_services(base_config) == []
    assert missing_services(
        EffectiveAIModelConfiguration.model_validate({"llm": DEFAULT_LLM})
    ) == [
        "tts",
        "stt",
    ]
    realtime_only = EffectiveAIModelConfiguration.model_validate({"is_realtime": True})
    assert missing_services(realtime_only) == ["realtime", "llm"]


# ----------------------------------------------------------- run resolution


@pytest.mark.asyncio
async def test_run_resolution_applies_stored_overrides(saved_profiles, base_config):
    with patch.object(
        overrides_module,
        "get_effective_ai_model_configuration_for_workflow",
        new=AsyncMock(return_value=base_config),
    ):
        effective = await get_effective_ai_model_configuration_for_run(
            organization_id=ORG_ID,
            workflow_configurations={},
            run_initial_context={
                RUN_MODEL_OVERRIDES_CONTEXT_KEY: {"llm": {"profile": "openai-cheap"}}
            },
        )
        untouched = await get_effective_ai_model_configuration_for_run(
            organization_id=ORG_ID,
            workflow_configurations={},
            run_initial_context={"x": 1},
        )

    assert effective.llm.model == "gpt-4.1-nano"
    assert untouched is base_config


@pytest.mark.asyncio
async def test_run_resolution_fails_loudly_when_a_profile_was_deleted(
    saved_profiles, base_config
):
    await profiles.delete_profile(ORG_ID, service="llm", name="openai-cheap")

    with (
        patch.object(
            overrides_module,
            "get_effective_ai_model_configuration_for_workflow",
            new=AsyncMock(return_value=base_config),
        ),
        pytest.raises(CallOverrideError, match="can no longer be applied"),
    ):
        await get_effective_ai_model_configuration_for_run(
            organization_id=ORG_ID,
            workflow_configurations={},
            run_initial_context={
                RUN_MODEL_OVERRIDES_CONTEXT_KEY: {"llm": {"profile": "openai-cheap"}}
            },
        )
