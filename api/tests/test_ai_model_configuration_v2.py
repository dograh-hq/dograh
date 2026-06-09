import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import (
    DograhManagedAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.schemas.user_configuration import EffectiveAIModelConfiguration
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
    check_for_masked_keys_in_ai_model_configuration_v2,
    convert_legacy_ai_model_configuration_to_v2,
    mask_ai_model_configuration_v2,
    merge_ai_model_configuration_v2_secrets,
    migrate_workflow_configuration_model_override_to_v2,
)
from api.services.configuration.masking import mask_key
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    DograhLLMService,
    DograhSTTService,
    DograhTTSService,
    ElevenlabsTTSConfiguration,
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
