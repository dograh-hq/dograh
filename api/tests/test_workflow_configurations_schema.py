import pytest
from pydantic import ValidationError

from api.constants import (
    MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
)
from api.schemas.workflow_configurations import (
    DEFAULT_MAX_CALL_DURATION_SECONDS,
    MAX_CALL_DURATION_SECONDS,
    TextChatInactivityTimeoutConstraints,
    WorkflowConfigurationDefaults,
)


def test_max_call_duration_default_within_bounds():
    config = WorkflowConfigurationDefaults()
    assert config.max_call_duration == DEFAULT_MAX_CALL_DURATION_SECONDS


def test_max_call_duration_accepts_cap():
    config = WorkflowConfigurationDefaults(max_call_duration=MAX_CALL_DURATION_SECONDS)
    assert config.max_call_duration == MAX_CALL_DURATION_SECONDS


def test_max_call_duration_rejects_over_cap():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(max_call_duration=MAX_CALL_DURATION_SECONDS + 1)


def test_max_call_duration_rejects_non_positive():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(max_call_duration=0)


def test_text_chat_inactivity_timeout_defaults_to_deployment_value():
    config = WorkflowConfigurationDefaults()

    assert (
        config.text_chat_inactivity_timeout_seconds
        == TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    )


def test_text_chat_inactivity_timeout_accepts_workflow_override():
    config = WorkflowConfigurationDefaults(text_chat_inactivity_timeout_seconds=15 * 60)

    assert config.text_chat_inactivity_timeout_seconds == 15 * 60


def test_text_chat_inactivity_timeout_rejects_values_below_minimum():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            text_chat_inactivity_timeout_seconds=(
                MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS - 1
            )
        )


def test_text_chat_inactivity_timeout_rejects_values_beyond_sweep_lookback():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            text_chat_inactivity_timeout_seconds=(
                MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS + 1
            )
        )


def test_text_chat_inactivity_timeout_bounds_are_exported_in_schema():
    field_schema = WorkflowConfigurationDefaults.model_json_schema()["properties"][
        "text_chat_inactivity_timeout_seconds"
    ]

    assert field_schema["minimum"] == MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    assert field_schema["maximum"] == MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS


def test_text_chat_inactivity_timeout_constraints_export_backend_constants():
    constraints = TextChatInactivityTimeoutConstraints()

    assert constraints.default_seconds == TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    assert constraints.minimum_seconds == MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS
    assert constraints.maximum_seconds == MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS


def test_null_values_treated_as_unset():
    """Stored configs / older clients send explicit JSON nulls for keys the
    user never configured; they must validate as defaults, not fail."""
    config = WorkflowConfigurationDefaults.model_validate(
        {
            "max_call_duration": None,
            "turn_start_strategy": None,
            "turn_start_min_words": None,
        }
    )
    assert config.max_call_duration == DEFAULT_MAX_CALL_DURATION_SECONDS
    # Nulls count as unset, so a sparse round-trip drops them entirely.
    assert config.model_dump(exclude_unset=True) == {}


def test_exclude_unset_round_trip_stays_sparse():
    config = WorkflowConfigurationDefaults.model_validate(
        {"max_call_duration": 600, "custom_extra_key": {"a": 1}}
    )
    assert config.model_dump(exclude_unset=True) == {
        "max_call_duration": 600,
        "custom_extra_key": {"a": 1},
    }


def test_cap_stays_within_concurrency_stale_timeout():
    """A call outliving the rate limiter's stale window has its concurrency
    slot purged mid-call, so the cap must never exceed it."""
    from api.services.call_concurrency.rate_limiter import rate_limiter

    assert MAX_CALL_DURATION_SECONDS <= rate_limiter.stale_call_timeout


def test_external_pbx_field_mapping_is_validated():
    config = WorkflowConfigurationDefaults(
        external_pbx_field_mappings=[
            {"context_path": " qualified ", "destination_field": " address3 "}
        ]
    )

    assert config.external_pbx_field_mappings[0].context_path == "qualified"
    assert config.external_pbx_field_mappings[0].destination_field == "address3"


def test_external_pbx_field_mapping_rejects_blank_context_paths():
    with pytest.raises(ValidationError, match="context_path"):
        WorkflowConfigurationDefaults(
            external_pbx_field_mappings=[
                {"context_path": "   ", "destination_field": "address3"}
            ]
        )


def test_external_pbx_field_mapping_rejects_invalid_field_names():
    with pytest.raises(ValidationError, match="destination_field"):
        WorkflowConfigurationDefaults(
            external_pbx_field_mappings=[
                {"context_path": "qualified", "destination_field": "invalid-field"}
            ]
        )
