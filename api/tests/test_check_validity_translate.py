"""Tests for translator-specific validation surfaces.

Two surfaces under test:

1. ``UserConfigurationValidator._validator_map`` — the translate provider
   is wired to the shared Google API-key checker (no-op stub today, but
   the *entry* must exist so an unknown-provider rejection doesn't fire
   when a workflow saves a translate realtime config).

2. ``_is_translator_workflow_publishable`` — the publish-time predicate
   that blocks workflows wiring Live Translate to prompts/tools/documents/
   global nodes.
"""

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    GoogleRealtimeTranslateLLMConfiguration,
    OpenAIRealtimeLLMConfiguration,
    ServiceProviders,
)
from api.services.workflow.errors import ItemKind
from api.services.workflow.translator_validation import (
    _is_translator_workflow_publishable,
)


# ---------------------------------------------------------------------------
# Validator map
# ---------------------------------------------------------------------------


def test_validator_map_has_translate_entry():
    validator = UserConfigurationValidator()
    entry = validator._validator_map.get(
        ServiceProviders.GOOGLE_REALTIME_TRANSLATE.value
    )
    # Reuses the Google API-key checker — translate accepts the same key
    # surface as the rest of the Gemini Live family.
    assert entry == validator._check_google_api_key


def test_validate_translate_realtime_config_returns_no_errors():
    validator = UserConfigurationValidator()
    cfg = EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=GoogleRealtimeTranslateLLMConfiguration(
            api_key="dummy", target_language_code="es"
        ),
    )
    errors = validator._validate_service(cfg.realtime, "realtime", required=True)
    assert errors == []


# ---------------------------------------------------------------------------
# Translator publish-time predicate
# ---------------------------------------------------------------------------


def _translate_config(target: str = "es") -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=GoogleRealtimeTranslateLLMConfiguration(
            api_key="dummy", target_language_code=target
        ),
    )


def _openai_realtime_config() -> EffectiveAIModelConfiguration:
    return EffectiveAIModelConfiguration(
        is_realtime=True,
        realtime=OpenAIRealtimeLLMConfiguration(api_key="dummy"),
    )


def _empty_node(node_id: str = "1", node_type: str = "startCall") -> dict:
    return {"id": node_id, "type": node_type, "data": {}}


def test_predicate_empty_workflow_translator_returns_no_errors():
    workflow = {"nodes": [_empty_node()], "edges": []}
    assert _is_translator_workflow_publishable(workflow, _translate_config()) == []


def test_predicate_returns_empty_when_non_translate_provider():
    # Workflow has prompts/tools/etc., but provider is not translate.
    workflow = {
        "nodes": [
            {
                "id": "1",
                "type": "agentNode",
                "data": {"prompt": "Hi", "tool_uuids": ["t1"]},
            }
        ],
        "edges": [],
    }
    assert (
        _is_translator_workflow_publishable(workflow, _openai_realtime_config()) == []
    )


def test_predicate_returns_empty_when_not_realtime():
    workflow = {
        "nodes": [{"id": "1", "type": "agentNode", "data": {"prompt": "Hi"}}],
        "edges": [],
    }
    non_realtime = EffectiveAIModelConfiguration(is_realtime=False)
    assert _is_translator_workflow_publishable(workflow, non_realtime) == []


def test_predicate_flags_prompt_violation():
    workflow = {
        "nodes": [
            {"id": "agent-1", "type": "agentNode", "data": {"prompt": "Be polite"}}
        ],
        "edges": [],
    }
    errors = _is_translator_workflow_publishable(workflow, _translate_config())
    assert len(errors) == 1
    assert errors[0]["kind"] == ItemKind.node
    assert errors[0]["id"] == "agent-1"
    assert errors[0]["field"] == "data.prompt"
    assert "prompts" in errors[0]["message"]


def test_predicate_flags_tool_and_document_violations():
    workflow = {
        "nodes": [
            {
                "id": "agent-2",
                "type": "agentNode",
                "data": {
                    "tool_uuids": ["tool-uuid-1"],
                    "document_uuids": ["doc-uuid-1"],
                },
            }
        ],
        "edges": [],
    }
    errors = _is_translator_workflow_publishable(workflow, _translate_config())
    fields = {e["field"] for e in errors}
    assert fields == {"data.tool_uuids", "data.document_uuids"}
    assert all(e["id"] == "agent-2" for e in errors)


def test_predicate_flags_global_node():
    workflow = {
        "nodes": [_empty_node(), {"id": "g", "type": "globalNode", "data": {}}],
        "edges": [],
    }
    errors = _is_translator_workflow_publishable(workflow, _translate_config())
    assert len(errors) == 1
    assert errors[0]["id"] == "g"
    assert errors[0]["field"] == "type"
    assert "global nodes" in errors[0]["message"]


def test_predicate_aggregates_violations_across_nodes():
    workflow = {
        "nodes": [
            {"id": "a", "type": "agentNode", "data": {"prompt": "x"}},
            {"id": "b", "type": "agentNode", "data": {"tool_uuids": ["t"]}},
            {"id": "g", "type": "globalNode", "data": {}},
        ],
        "edges": [],
    }
    errors = _is_translator_workflow_publishable(workflow, _translate_config())
    assert len(errors) == 3
    by_id = {e["id"]: e["field"] for e in errors}
    assert by_id == {
        "a": "data.prompt",
        "b": "data.tool_uuids",
        "g": "type",
    }
