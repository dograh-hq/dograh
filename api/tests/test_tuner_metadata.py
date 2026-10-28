"""Tuner sends only the call variables the user listed, as call metadata."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from loguru import logger
from pydantic import ValidationError

from api.services.integrations.base import IntegrationCompletionContext
from api.services.integrations.tuner.completion import run_completion
from api.services.integrations.tuner.node import SPEC, TunerNodeData
from api.services.workflow.dto import RFNodeDTO

SDK_META = {"ai_models": {"llm_model": "gpt"}, "usage_token": {"asr_duration": 1}}


@pytest.fixture
def warnings():
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    yield messages
    logger.remove(handler_id)


@pytest.fixture
def infos():
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    yield messages
    logger.remove(handler_id)


async def _deliver(
    *,
    enabled=True,
    keys="",
    initial_context=None,
    extracted=None,
    gathered_extra=None,
):
    gathered = {**(gathered_extra or {})}
    if extracted is not None:
        gathered["extracted_variables"] = extracted
    run = SimpleNamespace(
        id=42,
        gathered_context=gathered,
        initial_context=initial_context,
        logs={
            "tuner_payload": {
                "call_id": "42",
                "general_meta_data_raw": {
                    "ai_models": dict(SDK_META["ai_models"]),
                    "usage_token": dict(SDK_META["usage_token"]),
                },
            }
        },
        recording_url=None,
        usage_info=None,
    )
    node = {
        "id": "tuner",
        "data": {
            "name": "Tuner",
            "tuner_api_key": "test-key",
            "tuner_workspace_id": 1,
            "tuner_agent_id": "agent-1",
            "metadata_enabled": enabled,
            "metadata_keys": keys,
        },
    }
    post_call = AsyncMock(return_value={"status": "delivered"})
    with patch("api.services.integrations.tuner.completion.post_call", post_call):
        await run_completion(
            [node],
            IntegrationCompletionContext(
                workflow_run_id=42,
                workflow_run=run,
                workflow_definition={},
                definition_id=77,
                organization_id=11,
                public_token=None,
            ),
        )
    post_call.assert_awaited_once()
    return post_call.await_args.args[1]["general_meta_data_raw"]


async def test_toggle_off_leaves_payload_unchanged_even_with_keys():
    meta = await _deliver(
        enabled=False,
        keys=["account_status"],
        initial_context={"account_status": "active"},
    )
    assert meta == SDK_META


async def test_toggle_on_with_empty_list_leaves_payload_unchanged():
    meta = await _deliver(initial_context={"account_status": "active"})
    assert meta == SDK_META


async def test_listed_key_from_initial_context_is_sent():
    meta = await _deliver(
        keys=["account_status"], initial_context={"account_status": "active"}
    )
    assert meta["account_status"] == "active"
    assert {k: meta[k] for k in SDK_META} == SDK_META


async def test_listed_key_from_extracted_variables_is_sent():
    meta = await _deliver(keys=["loyalty_tier"], extracted={"loyalty_tier": "gold"})
    assert meta["loyalty_tier"] == "gold"


async def test_key_in_both_uses_initial_context():
    meta = await _deliver(
        keys=["loyalty_tier"],
        initial_context={"loyalty_tier": "from-precall"},
        extracted={"loyalty_tier": "from-extraction"},
    )
    assert meta["loyalty_tier"] == "from-precall"


@pytest.mark.parametrize("listed", ["Account_Status", "ACCOUNT_STATUS"])
async def test_key_matching_is_case_sensitive(listed):
    meta = await _deliver(
        keys=[listed],
        initial_context={"account_status": "active"},
        extracted={"account_status": "active"},
    )
    assert meta == SDK_META


async def test_unlisted_key_is_not_sent():
    meta = await _deliver(
        keys=["account_status"],
        initial_context={
            "account_status": "active",
            "date_of_birth": "1950-01-01",
        },
        extracted={"date_of_birth": "1950-01-01"},
    )
    assert "date_of_birth" not in meta


async def test_missing_key_is_skipped_and_logged(infos):
    meta = await _deliver(
        keys=["account_status", "nope", "also_nope"],
        initial_context={"account_status": "active"},
    )
    assert "nope" not in meta and "also_nope" not in meta
    lines = [m for m in infos if "not found in call context" in m]
    assert len(lines) == 1
    assert "nope, also_nope" in lines[0]
    assert "account_status" not in lines[0]


async def test_no_info_line_when_every_key_is_found(infos):
    await _deliver(keys=["a"], initial_context={"a": 1})
    assert not [m for m in infos if "not found in call context" in m]


async def test_existing_sdk_key_is_not_overwritten(warnings):
    meta = await _deliver(keys=["ai_models"], initial_context={"ai_models": "evil"})
    assert meta["ai_models"] == SDK_META["ai_models"]
    assert any("ai_models" in m for m in warnings)


async def test_nested_value_passes_through_unchanged():
    nested = {"plan": {"tier": "gold", "riders": ["a", "b"]}, "n": [1, {"x": None}]}
    meta = await _deliver(keys=["profile"], initial_context={"profile": nested})
    assert meta["profile"] == nested


@pytest.mark.parametrize("value", [0, False, "", None])
async def test_falsy_values_are_still_sent(value):
    meta = await _deliver(keys=["k"], initial_context={"k": value})
    assert "k" in meta and meta["k"] == value


async def test_missing_contexts_do_not_crash():
    meta = await _deliver(keys=["k"], initial_context=None, extracted=None)
    assert meta == SDK_META


def _validate(**fields):
    return TunerNodeData.model_validate(
        {"name": "Tuner", "tuner_enabled": False, **fields}
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("account_status, loyalty_tier", ["account_status", "loyalty_tier"]),
        ("account_status\nloyalty_tier", ["account_status", "loyalty_tier"]),
        ("account_status\r\nloyalty_tier", ["account_status", "loyalty_tier"]),
        ("a, b\nc,d\n e ", ["a", "b", "c", "d", "e"]),
        ("a\n\n  \n,, ,b\n", ["a", "b"]),
        ("a, b\na\n b ,a", ["a", "b"]),
        ("{{account_status}}", ["account_status"]),
        ("{{ loyalty_tier }}, {{a}}\n  {{ b }}  ", ["loyalty_tier", "a", "b"]),
        ("{{a}}, a", ["a"]),
        ("Account_Status, account_status", ["Account_Status", "account_status"]),
    ],
    ids=[
        "comma",
        "newline",
        "windows-newline",
        "mixed",
        "blank-lines-and-commas",
        "duplicates",
        "braces",
        "braces-with-spaces",
        "braced-duplicate",
        "case-differs-not-duplicate",
    ],
)
def test_text_is_split_trimmed_and_cleaned(text, expected):
    assert (
        _validate(metadata_enabled=True, metadata_keys=text).metadata_keys == expected
    )


@pytest.mark.parametrize(
    "text", ["customer plan", "ok, customer plan", "a\nb c\nd", "{{customer plan}}"]
)
def test_name_with_a_space_is_rejected_and_named(text):
    with pytest.raises(ValidationError) as exc_info:
        _validate(metadata_enabled=True, metadata_keys=text)
    message = str(exc_info.value)
    bad = "customer plan" if "customer plan" in text else "b c"
    assert f"Invalid variable name '{bad}'" in message
    assert "cannot contain spaces" in message


@pytest.mark.parametrize(
    "value",
    [None, "", "  \n , \n", []],
    ids=["null", "empty", "only-separators", "empty-list"],
)
def test_toggle_on_with_empty_field_saves(value):
    assert _validate(metadata_enabled=True, metadata_keys=value).metadata_keys == []


def test_field_omitted_defaults_to_empty():
    assert _validate(metadata_enabled=True).metadata_keys == []


def test_list_input_is_cleaned_the_same_way():
    data = _validate(
        metadata_enabled=True, metadata_keys=[" a ", "{{b}}", "a", "", "c"]
    )
    assert data.metadata_keys == ["a", "b", "c"]


@pytest.mark.parametrize(
    "text", ["customer plan", "ok, customer plan", "{{customer plan}}", "a b c"]
)
async def test_bad_name_with_toggle_off_still_validates_and_delivers(text):
    data = _validate(metadata_enabled=False, metadata_keys=text)
    assert all(" " not in name for name in data.metadata_keys)

    meta = await _deliver(
        enabled=False,
        keys=text + "\naccount_status",
        initial_context={"account_status": "active"},
    )
    assert meta == SDK_META


def test_bad_name_with_toggle_on_is_rejected_at_publish():
    node = {
        "id": "tuner",
        "type": "tuner",
        "position": {"x": 0, "y": 0},
        "data": {
            "name": "Tuner",
            "tuner_enabled": False,
            "metadata_enabled": True,
            "metadata_keys": "account_status, customer plan",
        },
    }
    with pytest.raises(ValidationError, match="Invalid variable name 'customer plan'"):
        RFNodeDTO.model_validate(node)

    node["data"]["metadata_enabled"] = False
    RFNodeDTO.model_validate(node)


@pytest.mark.parametrize(
    "value",
    [[1], [None], ["a", None], [True], [["x"]], [{"a": 1}], 5, {"a": 1}, True],
    ids=[
        "int",
        "none",
        "none-after-name",
        "bool",
        "nested-list",
        "dict-entry",
        "int-value",
        "dict-value",
        "bool-value",
    ],
)
def test_non_string_entries_are_rejected_with_a_validation_error(value):
    with pytest.raises(ValidationError) as exc_info:
        _validate(metadata_enabled=True, metadata_keys=value)
    assert "metadata_keys" in str(exc_info.value)
    assert not isinstance(exc_info.value, TypeError)


def test_non_string_entries_are_dropped_with_toggle_off():
    data = _validate(metadata_enabled=False, metadata_keys=["a", 1, None, "b"])
    assert data.metadata_keys == ["a", "b"]
    assert _validate(metadata_enabled=False, metadata_keys=5).metadata_keys == []


async def test_raw_text_as_saved_by_the_editor_is_delivered():
    meta = await _deliver(
        keys="{{account_status}}, loyalty_tier\nmissing\n",
        initial_context={"account_status": "active"},
        extracted={"loyalty_tier": "gold"},
    )
    assert meta["account_status"] == "active"
    assert meta["loyalty_tier"] == "gold"
    assert "missing" not in meta


def test_spec_exposes_metadata_fields_gated_by_toggle():
    props = {prop.name: prop for prop in SPEC.properties}
    toggle, keys = props["metadata_enabled"], props["metadata_keys"]
    assert toggle.type.value == "boolean"
    assert toggle.display_name == "Send call variables"
    assert keys.type.value == "string"
    assert keys.editor == "textarea"
    assert keys.display_name == "Variables to send"
    assert keys.placeholder == "e.g. account_status, loyalty_tier"
    assert keys.description == (
        "Variables from the call's initial context (including Pre-Call Data "
        "Fetch) or extracted variables. Separate names with commas or new "
        "lines. Variables you don't list are never sent."
    )
    assert keys.display_options.show == {"metadata_enabled": [True]}
    assert not keys.properties
    assert not keys.required
