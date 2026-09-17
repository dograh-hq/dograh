"""Google Search tool support for Gemini Live (Developer API).

Covers the opt-in ``google_search`` flag end to end without any live
network calls:

- registry: field exists, defaults to False, missing values load as False.
- factory: flag reaches ``DograhGeminiLiveLLMService``.
- session tools: Search is absent when OFF, present (additively) when ON,
  and survives tool republishing across node transitions.
"""

from types import SimpleNamespace

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from api.services.configuration.registry import GoogleRealtimeLLMConfiguration
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiLiveJSONSchemaAdapter,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.service_factory import create_realtime_llm_service

SEARCH_TOOL = {"google_search": {}}


class DummyUserConfig:
    def __init__(self, realtime_config):
        self.realtime = realtime_config


def _audio_config():
    return SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )


def _transition_function(name):
    return FunctionSchema(
        name=name,
        description=f"Transition via {name}.",
        properties={},
        required=[],
    )


def _node_tools(*names):
    return ToolsSchema(standard_tools=[_transition_function(n) for n in names])


def _make_service(**kwargs):
    return DograhGeminiLiveLLMService(
        api_key="test-key",
        settings=DograhGeminiLiveLLMService.Settings(
            model="gemini-3.1-flash-live-preview",
            voice="Puck",
        ),
        **kwargs,
    )


def _session_tools(service, tools_schema):
    # The exact funnel upstream _connect() uses for session configuration.
    return service.get_llm_adapter().from_standard_tools(tools_schema)


def _function_names(tools):
    for tool in tools:
        if isinstance(tool, dict) and "function_declarations" in tool:
            return [d["name"] for d in tool["function_declarations"]]
    return []


# ------------------------------------------------------------------
# Registry / persistence
# ------------------------------------------------------------------


def test_google_search_defaults_to_false_and_loads_missing_as_false():
    properties = GoogleRealtimeLLMConfiguration.model_json_schema()["properties"]
    assert properties["google_search"]["default"] is False
    # Existing saved configs without the key behave as OFF; no migration.
    assert GoogleRealtimeLLMConfiguration(api_key="k").google_search is False
    assert (
        GoogleRealtimeLLMConfiguration(api_key="k", google_search=True).google_search
        is True
    )


# ------------------------------------------------------------------
# Factory propagation
# ------------------------------------------------------------------


def test_factory_propagates_google_search_flag():
    service = create_realtime_llm_service(
        DummyUserConfig(
            GoogleRealtimeLLMConfiguration(api_key="k", google_search=True)
        ),
        _audio_config(),
    )
    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._google_search_enabled is True


def test_factory_defaults_google_search_to_off():
    service = create_realtime_llm_service(
        DummyUserConfig(GoogleRealtimeLLMConfiguration(api_key="k")),
        _audio_config(),
    )
    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._google_search_enabled is False


# ------------------------------------------------------------------
# Session tool payloads (A-D, F)
# ------------------------------------------------------------------


def test_search_absent_when_flag_missing():
    tools = _session_tools(_make_service(), _node_tools("go_to_b"))
    assert _function_names(tools) == ["go_to_b"]
    assert SEARCH_TOOL not in tools


def test_search_absent_when_flag_false():
    tools = _session_tools(_make_service(google_search=False), _node_tools("go_to_b"))
    assert _function_names(tools) == ["go_to_b"]
    assert SEARCH_TOOL not in tools


def test_search_absent_without_workflow_functions():
    assert _session_tools(_make_service(), ToolsSchema(standard_tools=[])) == []


def test_search_included_when_flag_true():
    tools = _session_tools(_make_service(google_search=True), _node_tools("go_to_b"))
    assert _function_names(tools) == ["go_to_b"]
    assert tools[-1] == SEARCH_TOOL


def test_search_included_without_workflow_functions():
    assert _session_tools(
        _make_service(google_search=True), ToolsSchema(standard_tools=[])
    ) == [SEARCH_TOOL]


def test_search_coexists_with_workflow_functions():
    tools = _session_tools(
        _make_service(google_search=True),
        _node_tools("answer", "go_to_billing", "end_call"),
    )
    assert _function_names(tools) == ["answer", "go_to_billing", "end_call"]
    assert tools[-1] == SEARCH_TOOL


def test_search_never_duplicated():
    service = _make_service(google_search=True)
    tools = _session_tools(service, _node_tools("go_to_b"))
    again = service.get_llm_adapter().from_standard_tools(
        ToolsSchema(
            standard_tools=[_transition_function("go_to_b")],
            custom_tools={"gemini": [dict(SEARCH_TOOL)]},
        )
    )
    assert again.count(SEARCH_TOOL) == 1
    assert tools.count(SEARCH_TOOL) == 1


def test_off_payload_matches_pre_feature_shape():
    # Behavioral identity with the pre-feature adapter: same formatting,
    # no Search entry.
    tools = _session_tools(_make_service(), _node_tools("go_to_b"))
    assert tools == DograhGeminiLiveJSONSchemaAdapter().to_provider_tools_format(
        _node_tools("go_to_b")
    )
    declaration = tools[0]["function_declarations"][0]
    assert declaration["name"] == "go_to_b"
    assert "parameters_json_schema" in declaration


# ------------------------------------------------------------------
# Reconnect / node-transition republishing (E)
# ------------------------------------------------------------------


def test_search_survives_node_transition_republish():
    service = _make_service(google_search=True)
    # Initial connection advertises node A's tools plus Search.
    initial = _session_tools(service, _node_tools("go_to_b", "end_call"))
    assert _function_names(initial) == ["go_to_b", "end_call"]
    assert initial[-1] == SEARCH_TOOL
    # After a node transition the reconnect republishes node B's tools;
    # Search must still be present alongside the NEW functions only.
    after = _session_tools(service, _node_tools("go_to_c", "escalate"))
    assert _function_names(after) == ["go_to_c", "escalate"]
    assert after[-1] == SEARCH_TOOL


def test_off_stays_off_across_republish():
    service = _make_service()
    for names in (["go_to_b"], ["go_to_c"]):
        tools = _session_tools(service, _node_tools(*names))
        assert _function_names(tools) == names
        assert SEARCH_TOOL not in tools
