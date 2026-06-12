from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    DograhManagedAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationResponse,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    check_for_masked_keys_in_ai_model_configuration_v2,
    convert_legacy_ai_model_configuration_to_v2,
    mask_ai_model_configuration_v2,
    merge_ai_model_configuration_v2_secrets,
    migrate_workflow_configuration_model_override_to_v2,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import mask_key
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    DograhLLMService,
    DograhSTTService,
    DograhTTSService,
    ElevenlabsTTSConfiguration,
    GoogleLLMService,
    GoogleRealtimeLLMConfiguration,
    OpenAIEmbeddingsConfiguration,
    OpenAILLMService,
)


def test_dograh_v2_compiles_to_effective_managed_pipeline_with_embeddings():
    config = OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(
            api_key="mps-secret",
            voice="default",
            speed=1.2,
            language="multi",
        ),
    )

    effective = compile_ai_model_configuration_v2(config)

    assert effective.is_realtime is False
    assert effective.llm.provider == "dograh"
    assert effective.llm.model == "default"
    assert effective.tts.provider == "dograh"
    assert effective.tts.speed == 1.2
    assert effective.stt.provider == "dograh"
    assert effective.stt.language == "multi"
    assert effective.embeddings.provider == "dograh"
    assert effective.embeddings.model == "default"
    assert effective.managed_service_version == 2


def test_dograh_v2_rejects_non_predefined_speed():
    with pytest.raises(ValidationError):
        OrganizationAIModelConfigurationV2(
            mode="dograh",
            dograh=DograhManagedAIModelConfiguration(
                api_key="mps-secret",
                speed=1.5,
            ),
        )


def test_byok_v2_rejects_dograh_provider():
    with pytest.raises(ValidationError):
        OrganizationAIModelConfigurationV2.model_validate(
            {
                "mode": "byok",
                "byok": {
                    "mode": "pipeline",
                    "pipeline": {
                        "llm": {
                            "provider": "dograh",
                            "api_key": "mps-secret",
                            "model": "default",
                        },
                        "tts": {
                            "provider": "dograh",
                            "api_key": "mps-secret",
                            "model": "default",
                            "voice": "default",
                        },
                        "stt": {
                            "provider": "dograh",
                            "api_key": "mps-secret",
                            "model": "default",
                        },
                    },
                },
            }
        )


@pytest.mark.asyncio
async def test_byok_realtime_validator_does_not_require_stt_or_tts():
    config = OrganizationAIModelConfigurationV2.model_validate(
        {
            "mode": "byok",
            "byok": {
                "mode": "realtime",
                "realtime": {
                    "realtime": {
                        "provider": "google_realtime",
                        "api_key": "google-realtime-key",
                        "model": "gemini-3.1-flash-live-preview",
                        "voice": "Puck",
                        "language": "en",
                    },
                    "llm": {
                        "provider": "google",
                        "api_key": "google-llm-key",
                        "model": "gemini-2.0-flash",
                    },
                },
            },
        }
    )
    effective = compile_ai_model_configuration_v2(config)

    assert effective.is_realtime is True
    assert effective.stt is None
    assert effective.tts is None
    assert await UserConfigurationValidator().validate(effective) == {
        "status": [{"model": "all", "message": "ok"}]
    }


@pytest.mark.asyncio
async def test_pipeline_validator_requires_stt_and_tts_when_not_realtime():
    effective = EffectiveAIModelConfiguration(
        llm=GoogleLLMService(
            provider="google",
            api_key="google-llm-key",
            model="gemini-2.0-flash",
        ),
        realtime=GoogleRealtimeLLMConfiguration(
            provider="google_realtime",
            api_key="google-realtime-key",
            model="gemini-3.1-flash-live-preview",
            voice="Puck",
            language="en",
        ),
        is_realtime=False,
    )

    with pytest.raises(ValueError) as exc_info:
        await UserConfigurationValidator().validate(effective)

    assert exc_info.value.args[0] == [
        {"model": "stt", "message": "API key is missing"},
        {"model": "tts", "message": "API key is missing"},
    ]


def test_masked_dograh_key_is_preserved_when_saving_same_mode():
    existing = OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(api_key="mps-real-secret"),
    )
    incoming = OrganizationAIModelConfigurationV2(
        mode="dograh",
        dograh=DograhManagedAIModelConfiguration(api_key=mask_key("mps-real-secret")),
    )

    merged = merge_ai_model_configuration_v2_secrets(incoming, existing)

    assert merged.dograh.api_key == "mps-real-secret"
    check_for_masked_keys_in_ai_model_configuration_v2(merged)


def test_masked_v2_configuration_masks_nested_service_keys():
    config = OrganizationAIModelConfigurationV2(
        mode="byok",
        byok={
            "mode": "pipeline",
            "pipeline": {
                "llm": {
                    "provider": "openai",
                    "api_key": "sk-real-secret",
                    "model": "gpt-4.1",
                },
                "tts": {
                    "provider": "elevenlabs",
                    "api_key": "el-real-secret",
                    "model": "eleven_flash_v2_5",
                    "voice": "Rachel",
                },
                "stt": {
                    "provider": "deepgram",
                    "api_key": "dg-real-secret",
                    "model": "nova-3-general",
                },
            },
        },
    )

    masked = mask_ai_model_configuration_v2(config)

    assert masked["byok"]["pipeline"]["llm"]["api_key"] == mask_key("sk-real-secret")
    assert masked["byok"]["pipeline"]["tts"]["api_key"] == mask_key("el-real-secret")
    assert masked["byok"]["pipeline"]["stt"]["api_key"] == mask_key("dg-real-secret")


def test_legacy_all_dograh_pipeline_converts_to_dograh_v2():
    legacy = EffectiveAIModelConfiguration(
        llm=DograhLLMService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
        ),
        tts=DograhTTSService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
            voice="default",
            speed=1.0,
        ),
        stt=DograhSTTService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
            language="multi",
        ),
    )

    config = convert_legacy_ai_model_configuration_to_v2(legacy)

    assert config.mode == "dograh"
    assert config.dograh.api_key == "mps-secret"


def test_legacy_mixed_dograh_pipeline_converts_to_dograh_v2():
    legacy = EffectiveAIModelConfiguration(
        llm=OpenAILLMService(
            provider="openai",
            api_key="sk-llm",
            model="gpt-4.1",
        ),
        tts=DograhTTSService(
            provider="dograh",
            api_key="mps-tts",
            model="default",
            voice="default",
        ),
        stt=DograhSTTService(
            provider="dograh",
            api_key="mps-stt",
            model="default",
        ),
        embeddings=OpenAIEmbeddingsConfiguration(
            provider="openai",
            api_key="sk-emb",
            model="text-embedding-3-small",
        ),
    )

    config = convert_legacy_ai_model_configuration_to_v2(legacy)

    assert config.mode == "dograh"
    assert config.dograh.api_key == "mps-tts"
    assert config.dograh.voice == "default"


def test_legacy_byok_pipeline_converts_to_byok_v2():
    legacy = EffectiveAIModelConfiguration(
        llm=OpenAILLMService(
            provider="openai",
            api_key="sk-llm",
            model="gpt-4.1",
        ),
        tts=ElevenlabsTTSConfiguration(
            provider="elevenlabs",
            api_key="el-tts",
            model="eleven_flash_v2_5",
            voice="Rachel",
        ),
        stt=DeepgramSTTConfiguration(
            provider="deepgram",
            api_key="dg-stt",
            model="nova-3-general",
        ),
        embeddings=OpenAIEmbeddingsConfiguration(
            provider="openai",
            api_key="sk-emb",
            model="text-embedding-3-small",
        ),
    )

    config = convert_legacy_ai_model_configuration_to_v2(legacy)

    assert config.mode == "byok"
    assert config.byok.mode == "pipeline"
    assert config.byok.pipeline.llm.provider == "openai"
    assert config.byok.pipeline.tts.provider == "elevenlabs"


def test_workflow_model_override_migration_removes_v1_override_and_sets_v2():
    base = EffectiveAIModelConfiguration(
        llm=OpenAILLMService(
            provider="openai",
            api_key="sk-llm",
            model="gpt-4.1",
        ),
        tts=ElevenlabsTTSConfiguration(
            provider="elevenlabs",
            api_key="el-tts",
            model="eleven_flash_v2_5",
            voice="Rachel",
        ),
        stt=DeepgramSTTConfiguration(
            provider="deepgram",
            api_key="dg-stt",
            model="nova-3-general",
        ),
    )
    workflow_configurations = {
        "ambient_noise_configuration": {"enabled": False},
        "model_overrides": {
            "tts": {
                "provider": "dograh",
                "api_key": "mps-workflow",
                "model": "default",
                "voice": "default",
            }
        },
    }

    migrated, changed = migrate_workflow_configuration_model_override_to_v2(
        workflow_configurations,
        base,
    )

    assert changed is True
    assert "model_overrides" not in migrated
    assert migrated["ambient_noise_configuration"] == {"enabled": False}
    v2_override = migrated[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]
    assert v2_override["mode"] == "dograh"
    assert v2_override["dograh"]["api_key"] == "mps-workflow"


def test_workflow_model_override_migration_removes_invalid_v1_override_marker():
    base = EffectiveAIModelConfiguration()
    workflow_configurations = {
        "ambient_noise_configuration": {"enabled": False},
        "model_overrides": None,
    }

    migrated, changed = migrate_workflow_configuration_model_override_to_v2(
        workflow_configurations,
        base,
    )

    assert changed is True
    assert "model_overrides" not in migrated
    assert migrated["ambient_noise_configuration"] == {"enabled": False}


@pytest.mark.asyncio
async def test_migrate_model_configuration_v2_initializes_hosted_mps_billing(
    monkeypatch,
):
    from api.routes import organization as organization_routes

    legacy = EffectiveAIModelConfiguration(
        llm=DograhLLMService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
        ),
        tts=DograhTTSService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
            voice="default",
        ),
        stt=DograhSTTService(
            provider="dograh",
            api_key=["mps-secret"],
            model="default",
        ),
    )
    expected_response = OrganizationAIModelConfigurationResponse(
        configuration={"version": 2, "mode": "dograh"},
        effective_configuration={},
        source="organization_v2",
    )

    class FakeValidator:
        async def validate(self, *args, **kwargs):
            return {"status": [{"model": "all", "message": "ok"}]}

    ensure_billing = AsyncMock(return_value={"billing_mode": "v2"})
    upsert = AsyncMock()
    migrate_workflows = AsyncMock()

    monkeypatch.setattr(organization_routes, "DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        organization_routes,
        "get_organization_ai_model_configuration_v2",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        organization_routes.db_client,
        "get_user_configurations",
        AsyncMock(return_value=legacy),
    )
    monkeypatch.setattr(
        organization_routes,
        "UserConfigurationValidator",
        lambda: FakeValidator(),
    )
    monkeypatch.setattr(
        organization_routes,
        "ensure_hosted_mps_billing_account_v2",
        ensure_billing,
    )
    monkeypatch.setattr(
        organization_routes,
        "upsert_organization_ai_model_configuration_v2",
        upsert,
    )
    monkeypatch.setattr(
        organization_routes,
        "migrate_workflow_model_configurations_to_v2",
        migrate_workflows,
    )
    monkeypatch.setattr(
        organization_routes,
        "_model_configuration_v2_response",
        AsyncMock(return_value=expected_response),
    )

    user = SimpleNamespace(
        id=7,
        provider_id="provider-123",
        selected_organization_id=42,
    )

    response = await organization_routes.migrate_model_configuration_v2(
        force=False,
        user=user,
    )

    ensure_billing.assert_awaited_once_with(42, created_by="provider-123")
    upsert.assert_awaited_once()
    migrate_workflows.assert_awaited_once_with(
        organization_id=42,
        fallback_user_config=legacy,
    )
    assert response == expected_response
