import pytest
from pydantic import ValidationError

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.masking import is_mask_of, mask_key, mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.registry import (
    OpenAILLMService,
)

REAL_KEY = "sk-1234567890abcdef"


def _build_config_with_openai(key: str) -> UserConfiguration:
    return UserConfiguration(
        llm=OpenAILLMService(api_key=key),
        stt=None,
        tts=None,
    )


def test_mask_key_basic():
    masked = mask_key(REAL_KEY)
    # Should reveal only last 4 chars
    assert masked.endswith(REAL_KEY[-4:])
    assert set(masked[:-4]) == {"*"}
    assert len(masked) == len(REAL_KEY)
    # is_mask_of round-trip
    assert is_mask_of(masked, REAL_KEY)


def test_mask_user_config_masks_api_keys():
    cfg = _build_config_with_openai(REAL_KEY)
    dumped = mask_user_config(cfg)
    assert dumped["llm"]["api_key"].endswith(REAL_KEY[-4:])
    assert dumped["llm"]["api_key"].startswith("*" * (len(REAL_KEY) - 4))


def test_merge_preserves_key_when_mask_sent():
    existing = _build_config_with_openai(REAL_KEY)
    incoming_partial = {
        "llm": {
            "provider": "openai",
            "model": existing.llm.model,
            "api_key": mask_key(REAL_KEY),  # masked placeholder
        }
    }

    merged = merge_user_configurations(existing, incoming_partial)
    assert merged.llm.api_key == REAL_KEY  # key preserved


def test_merge_replaces_key_when_new_key_provided():
    existing = _build_config_with_openai(REAL_KEY)
    new_key = "sk-replaced-9999"
    incoming_partial = {
        "llm": {
            "provider": "openai",
            "model": existing.llm.model,
            "api_key": new_key,
        }
    }
    merged = merge_user_configurations(existing, incoming_partial)
    assert merged.llm.api_key == new_key


def test_merge_drops_old_key_when_provider_changes():
    existing = _build_config_with_openai(REAL_KEY)
    incoming_partial = {
        "llm": {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            # api_key intentionally absent – should NOT inherit old key
        }
    }

    with pytest.raises(ValidationError):
        merge_user_configurations(existing, incoming_partial)
