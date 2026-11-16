from types import SimpleNamespace

import pytest
from pipecat.utils.types import is_given
from pipecat_gnani import GnaniSTTService
from pydantic import ValidationError

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.masking import mask_user_config
from api.services.configuration.registry import GnaniSTTConfiguration
from api.services.pipecat.service_factory import (
    create_stt_service,
    get_stt_settings_update,
)


def test_gnani_defaults():
    config = GnaniSTTConfiguration(api_key="test-key")
    assert config.model == "prisma-v2.5"
    assert config.language == "hi-IN"
    assert config.language_candidates is None
    assert config.keywords is None


def test_gnani_rejects_unknown_model():
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(api_key="test-key", model="prisma-v9")


def test_gnani_rejects_indic_auto_on_v25():
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key", model="prisma-v2.5", language="indic-auto"
        )


def test_gnani_rejects_v25_keywords_and_candidates():
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key",
            model="prisma-v2.5",
            language="hi-IN",
            keywords=["Dograh"],
        )
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key",
            model="prisma-v2.5",
            language="hi-IN",
            language_candidates=["hi-IN", "en-IN"],
        )


def test_gnani_rejects_bad_candidate_count_and_keyword_limit():
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key",
            model="prisma-v3.0",
            language="indic-auto",
            language_candidates=["hi-IN"],
        )
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key",
            model="prisma-v3.0",
            language="hi-IN",
            keywords=[f"word-{index}" for index in range(201)],
        )


def test_gnani_rejects_keyword_markers():
    with pytest.raises(ValidationError):
        GnaniSTTConfiguration(
            api_key="test-key",
            model="prisma-v3.0",
            language="hi-IN",
            keywords=["<|start|>"],
        )


def test_gnani_v3_round_trip_and_masks_api_key():
    config = GnaniSTTConfiguration(
        api_key="gnani-secret-key-1234",
        model="prisma-v3.0",
        language="indic-auto",
        language_candidates=["hi-IN", "en-IN"],
        keywords=["Dograh"],
    )
    restored = GnaniSTTConfiguration.model_validate(config.model_dump())
    assert restored.language_candidates == ["hi-IN", "en-IN"]
    assert restored.keywords == ["Dograh"]
    masked = mask_user_config(EffectiveAIModelConfiguration(stt=config))
    assert "gnani-secret-key-1234" not in str(masked)
    assert masked["stt"]["api_key"].endswith("1234")


@pytest.mark.parametrize("sample_rate", [8000, 16000])
def test_create_gnani_stt_service_without_connecting(sample_rate):
    config = GnaniSTTConfiguration(api_key="test-key", language="hi-IN")
    service = create_stt_service(
        SimpleNamespace(stt=config),
        SimpleNamespace(transport_in_sample_rate=sample_rate),
    )
    assert isinstance(service, GnaniSTTService)
    assert service._ws is None
    assert service._init_sample_rate == sample_rate
    assert service._settings.model == "prisma-v2.5"
    assert service._settings.language == "hi-IN"


def test_transfer_clears_candidates_and_keywords():
    source = GnaniSTTConfiguration(
        api_key="test-key",
        model="prisma-v3.0",
        language="indic-auto",
        language_candidates=["hi-IN", "en-IN"],
        keywords=["Dograh"],
    )
    destination = GnaniSTTConfiguration(
        api_key="test-key",
        model="prisma-v3.0",
        language="hi-IN",
    )
    service = create_stt_service(
        SimpleNamespace(stt=source),
        SimpleNamespace(transport_in_sample_rate=16000),
    )
    delta = get_stt_settings_update(service, source, destination)
    assert delta is not None
    fields = delta.given_fields()
    assert fields["language"] == "hi-IN"
    assert fields["language_candidates"] is None
    assert is_given(delta.language_candidates)
    assert fields["keywords"] is None
    assert is_given(delta.keywords)
