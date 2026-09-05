import pytest
from pydantic import ValidationError

from api.constants import (
    MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
    TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS,
)
from api.schemas.workflow_configurations import (
    DEFAULT_CALL_DISPOSITION_OPTIONS,
    DEFAULT_MAX_CALL_DURATION_SECONDS,
    MAX_CALL_DISPOSITION_CODE_LENGTH,
    MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH,
    MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH,
    MAX_CALL_DISPOSITIONS,
    MAX_CALL_DURATION_SECONDS,
    MAX_EXTERNAL_PBX_LEAD_HEADERS,
    TextChatInactivityTimeoutConstraints,
    WorkflowConfigurationDefaults,
    get_default_call_disposition_options,
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


def test_call_dispositions_are_trimmed():
    configured = WorkflowConfigurationDefaults(
        call_dispositions=[
            {
                "code": "  call_rescheduled  ",
                "description": "  The caller booked another conversation.  ",
            }
        ]
    )

    assert configured.call_dispositions[0].code == "call_rescheduled"
    assert configured.call_dispositions[0].description == (
        "The caller booked another conversation."
    )


def test_default_call_dispositions_are_complete_and_returned_as_fresh_models():
    first = get_default_call_disposition_options()
    second = get_default_call_disposition_options()

    assert [option.code for option in first] == [
        "qualified",
        "not_interested",
        "wrong_number",
        "voicemail_detected",
        "do_not_call",
        "callback_requested",
    ]
    assert all(option.description for option in first)
    assert tuple(first) == DEFAULT_CALL_DISPOSITION_OPTIONS
    assert all(left is not right for left, right in zip(first, second, strict=True))


def test_call_dispositions_have_a_bounded_row_count():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            call_dispositions=[
                {"code": f"outcome_{index}", "description": "Description."}
                for index in range(MAX_CALL_DISPOSITIONS + 1)
            ]
        )


def test_call_disposition_code_has_a_bounded_size():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            call_dispositions=[
                {
                    "code": "x" * (MAX_CALL_DISPOSITION_CODE_LENGTH + 1),
                    "description": "A valid description.",
                }
            ]
        )


def test_call_disposition_description_has_a_bounded_size():
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            call_dispositions=[
                {
                    "code": "qualified",
                    "description": "x" * (MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH + 1),
                }
            ]
        )


def test_call_disposition_descriptions_have_a_total_budget():
    full_description = "x" * MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH
    overflow = "x" * (
        MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH
        - (4 * MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH)
        + 1
    )
    with pytest.raises(ValidationError, match="descriptions must total"):
        WorkflowConfigurationDefaults(
            call_dispositions=[
                {"code": f"outcome_{index}", "description": full_description}
                for index in range(4)
            ]
            + [
                {"code": "overflow", "description": overflow},
            ]
        )


def test_call_disposition_codes_are_unique_case_insensitively():
    with pytest.raises(ValidationError, match="codes must be unique"):
        WorkflowConfigurationDefaults(
            call_dispositions=[
                {"code": "qualified", "description": "Qualified."},
                {"code": "QUALIFIED", "description": "Also qualified."},
            ]
        )


@pytest.mark.parametrize("code", ["not interested", "123", "qualified!"])
def test_call_disposition_codes_use_machine_safe_format(code):
    with pytest.raises(ValidationError):
        WorkflowConfigurationDefaults(
            call_dispositions=[{"code": code, "description": "Description."}]
        )


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


def test_external_pbx_lead_headers_default_to_empty():
    assert WorkflowConfigurationDefaults().external_pbx_lead_headers == []


def test_external_pbx_lead_headers_are_stripped_and_deduplicated_in_order():
    config = WorkflowConfigurationDefaults(
        external_pbx_lead_headers=["  first_name  ", "address1", "first_name", "", "  "]
    )

    assert config.external_pbx_lead_headers == ["first_name", "address1"]


@pytest.mark.parametrize(
    "field",
    [
        "1first_name",  # must start with a letter
        "first name",  # no spaces
        "first-name",  # no dashes
        "PJSIP_HEADER(read,x))",  # function-injection shaped
        "a" * 65,  # over the length cap
    ],
)
def test_external_pbx_lead_headers_reject_unsafe_field_names(field):
    with pytest.raises(ValidationError, match="external_pbx_lead_headers"):
        WorkflowConfigurationDefaults(external_pbx_lead_headers=[field])


def test_external_pbx_lead_headers_reject_oversized_list():
    fields = [f"field_{index}" for index in range(MAX_EXTERNAL_PBX_LEAD_HEADERS + 1)]

    with pytest.raises(ValidationError, match="external_pbx_lead_headers"):
        WorkflowConfigurationDefaults(external_pbx_lead_headers=fields)


def test_external_pbx_lead_headers_treat_null_as_unset():
    config = WorkflowConfigurationDefaults.model_validate(
        {"external_pbx_lead_headers": None}
    )

    assert config.external_pbx_lead_headers == []
