"""Tests for widget-supplied context sanitization.

Context arrives from a third-party page, so the sanitizer caps it rather than
rejecting it: a bad entry is dropped, the rest of the conversation still starts.
"""

from api.services.workflow.embed_context import (
    MAX_CONTEXT_VARIABLES,
    MAX_KEY_LENGTH,
    MAX_VALUE_LENGTH,
    sanitize_embed_context_variables,
)


def test_none_and_empty_yield_empty_dict():
    assert sanitize_embed_context_variables(None) == {}
    assert sanitize_embed_context_variables({}) == {}
    assert sanitize_embed_context_variables("customer_name=Ada") == {}


def test_scalars_pass_through():
    raw = {
        "customer_name": "Abhishek",
        "plan_tier": 3,
        "balance": 12.5,
        "is_trial": True,
        "referrer": None,
    }
    assert sanitize_embed_context_variables(raw) == raw


def test_keys_are_stripped_and_bad_keys_dropped():
    result = sanitize_embed_context_variables(
        {
            "  customer_name  ": "Ada",
            "   ": "blank",
            "": "empty",
            "x" * (MAX_KEY_LENGTH + 1): "too long",
            42: "not a string",
        }
    )
    assert result == {"customer_name": "Ada"}


def test_template_unaddressable_keys_are_dropped():
    result = sanitize_embed_context_variables(
        {
            "customer.name": "dot is nested traversal",
            "customer name": "whitespace terminates the variable name",
            "customer|name": "pipe starts a fallback",
            "customer{name": "brace is a template delimiter",
            "customer}name": "brace is a template delimiter",
            "customer-name": "Ada",
        }
    )
    assert result == {"customer-name": "Ada"}


def test_reserved_keys_are_dropped():
    result = sanitize_embed_context_variables(
        {
            "provider": "twilio",
            "runtime_configuration": {"llm_model": "evil"},
            "mps_correlation_id": "visitor-controlled",
            "customer_name": "Ada",
        }
    )
    assert result == {"customer_name": "Ada"}


def test_long_string_values_are_truncated():
    result = sanitize_embed_context_variables({"bio": "a" * (MAX_VALUE_LENGTH + 500)})
    assert len(result["bio"]) == MAX_VALUE_LENGTH


def test_nested_values_kept_when_small_and_dropped_when_large():
    small = {"address": {"city": "Bengaluru"}, "tags": ["vip", "renewal"]}
    assert sanitize_embed_context_variables(small) == small

    oversized = {"history": ["x" * 100 for _ in range(100)]}
    assert sanitize_embed_context_variables(oversized) == {}


def test_unsupported_value_types_are_dropped():
    result = sanitize_embed_context_variables(
        {"callback": object(), "customer_name": "Ada"}
    )
    assert result == {"customer_name": "Ada"}


def test_variable_count_is_capped():
    raw = {f"key_{i}": i for i in range(MAX_CONTEXT_VARIABLES + 10)}
    result = sanitize_embed_context_variables(raw)
    assert len(result) == MAX_CONTEXT_VARIABLES
    # Earlier keys win, so the cap is deterministic for a given payload.
    assert result["key_0"] == 0
    assert f"key_{MAX_CONTEXT_VARIABLES}" not in result


def test_total_payload_is_capped():
    raw = {f"key_{i}": "x" * MAX_VALUE_LENGTH for i in range(20)}
    result = sanitize_embed_context_variables(raw)
    assert 0 < len(result) < 20
