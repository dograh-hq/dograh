"""Registry-level tests for the Live Translate realtime provider.

Pins the provider's presence in REGISTRY/REALTIME_PROVIDERS, validates
the schema surface exposed to the frontend (``target_language_code``
field, model literal, provider docstring), and confirms the discriminated
union in ``RealtimeConfig`` round-trips a translate config without loss.
"""

import pytest
from pydantic import ValidationError

from api.services.configuration.registry import (
    REALTIME_PROVIDERS,
    REGISTRY,
    GoogleRealtimeTranslateLLMConfiguration,
    ServiceProviders,
    ServiceType,
)


def test_translate_provider_registered_for_realtime():
    assert ServiceProviders.GOOGLE_REALTIME_TRANSLATE.value in REALTIME_PROVIDERS
    registry_entry = REGISTRY[ServiceType.REALTIME].get(
        ServiceProviders.GOOGLE_REALTIME_TRANSLATE
    )
    assert registry_entry is GoogleRealtimeTranslateLLMConfiguration


def test_translate_provider_value_matches_enum():
    # Wire value the UI and DB persist; do not change without a migration.
    assert (
        ServiceProviders.GOOGLE_REALTIME_TRANSLATE.value == "google_realtime_translate"
    )


def test_translate_config_defaults_and_target_language_code_field():
    cfg = GoogleRealtimeTranslateLLMConfiguration(api_key="dummy")
    assert cfg.provider == ServiceProviders.GOOGLE_REALTIME_TRANSLATE
    assert cfg.model == "gemini-3.5-live-translate-preview"
    # Default chosen to mirror Gemini SDK; UI overrides per-workflow.
    assert cfg.target_language_code == "en"


def test_translate_config_accepts_custom_target_language_code():
    cfg = GoogleRealtimeTranslateLLMConfiguration(
        api_key="dummy", target_language_code="pt-BR"
    )
    assert cfg.target_language_code == "pt-BR"


def test_translate_config_rejects_non_translate_model():
    # Literal pin guards against silently flipping to a non-translate
    # variant when a customer edits raw JSON.
    with pytest.raises(ValidationError):
        GoogleRealtimeTranslateLLMConfiguration(
            api_key="dummy", model="gemini-2.5-flash-live-preview"
        )


def test_translate_schema_exposes_target_language_code_examples():
    schema = GoogleRealtimeTranslateLLMConfiguration.model_json_schema()
    lang_field = schema["properties"]["target_language_code"]
    assert "examples" in lang_field
    assert isinstance(lang_field["examples"], list)
    assert len(lang_field["examples"]) > 0
    assert lang_field.get("allow_custom_input") is True


def test_translate_schema_pins_model_literal():
    schema = GoogleRealtimeTranslateLLMConfiguration.model_json_schema()
    model_field = schema["properties"]["model"]
    # Literal types serialize to a const or single-element enum depending
    # on pydantic version; accept either.
    assert model_field.get(
        "const"
    ) == "gemini-3.5-live-translate-preview" or model_field.get("enum") == [
        "gemini-3.5-live-translate-preview"
    ]


def test_translate_round_trips_through_realtime_union():
    """A translate config must survive the discriminated-union round-trip
    used to persist/restore EffectiveAIModelConfiguration.realtime."""
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration

    payload = {
        "is_realtime": True,
        "realtime": {
            "provider": ServiceProviders.GOOGLE_REALTIME_TRANSLATE.value,
            "api_key": "dummy",
            "model": "gemini-3.5-live-translate-preview",
            "target_language_code": "es",
        },
    }
    effective = EffectiveAIModelConfiguration.model_validate(payload)
    assert effective.is_realtime is True
    assert isinstance(effective.realtime, GoogleRealtimeTranslateLLMConfiguration)
    assert effective.realtime.target_language_code == "es"

    # Round-trip back to dict and re-validate.
    re_dumped = effective.model_dump()
    again = EffectiveAIModelConfiguration.model_validate(re_dumped)
    assert isinstance(again.realtime, GoogleRealtimeTranslateLLMConfiguration)
    assert again.realtime.target_language_code == "es"
