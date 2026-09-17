"""Reasoning-effort normalization for OpenAI pipeline LLMs.

Regression coverage: gpt-5.6-luna rejects reasoning_effort="minimal"
(HTTP 400 unsupported_value), which broke shared inference/extraction calls.
"""

import pytest

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import (
    _normalize_reasoning_effort,
    create_llm_service_from_provider,
)


def _luna_service(**kwargs):
    return create_llm_service_from_provider(
        ServiceProviders.OPENAI.value,
        "gpt-5.6-luna",
        "test-key",
        **kwargs,
    )


def test_luna_minimal_normalizes_to_low():
    assert _normalize_reasoning_effort("gpt-5.6-luna", "minimal") == "low"


def test_luna_matching_is_case_insensitive():
    assert _normalize_reasoning_effort("GPT-5.6-LUNA", "minimal") == "low"


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_luna_valid_values_pass_through(effort):
    assert _normalize_reasoning_effort("gpt-5.6-luna", effort) == effort


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5.4-mini", "o4-mini"])
def test_models_with_minimal_support_keep_minimal(model):
    assert _normalize_reasoning_effort(model, "minimal") == "minimal"


def test_luna_service_extra_contains_normalized_effort():
    service = _luna_service()
    assert service._settings.extra["reasoning_effort"] == "low"
    assert service._settings.extra["verbosity"] == "low"


def test_other_gpt5_service_keeps_minimal():
    service = create_llm_service_from_provider(
        ServiceProviders.OPENAI.value,
        "gpt-5",
        "test-key",
    )
    assert service._settings.extra["reasoning_effort"] == "minimal"
