"""Gemini 3.8 Live (base model) compatibility.

- registry accepts the 3.8 model ID without changing the original Realtime
  examples, and exposes no thinking_level field.
- factory passes the model string and google_search through.
- base gemini-3.8-live gets explicit BLOCKING function declarations
  (async is the new server default there); older models are untouched.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from api.services.configuration.registry import GoogleRealtimeLLMConfiguration
from api.services.pipecat.realtime.gemini_live import (
    DograhGeminiLiveLLMService,
    live_node_header,
)
from api.services.pipecat.service_factory import create_realtime_llm_service

BASE = DograhGeminiLiveLLMService.GEMINI_38_LIVE_MODEL
LEGACY = "gemini-3.1-flash-live-preview"


class DummyUserConfig:
    def __init__(self, realtime_config):
        self.realtime = realtime_config


def _audio_config():
    return SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )


def _make_service(model, **kwargs):
    return DograhGeminiLiveLLMService(
        api_key="test-key",
        settings=DograhGeminiLiveLLMService.Settings(model=model, voice="Puck"),
        **kwargs,
    )


def _tools(service):
    return service.get_llm_adapter().from_standard_tools(
        ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="go_to_b",
                    description="Transition.",
                    properties={},
                    required=[],
                )
            ]
        )
    )


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


def test_registry_accepts_38_model_id_without_changing_realtime_examples():
    schema = GoogleRealtimeLLMConfiguration.model_json_schema()["properties"]
    # Original Realtime tab still lists only 3.1.
    assert schema["model"]["examples"] == ["gemini-3.1-flash-live-preview"]
    # Backend validation accepts arbitrary model strings (plain str field).
    assert GoogleRealtimeLLMConfiguration(api_key="k", model=BASE).model == BASE
    assert GoogleRealtimeLLMConfiguration(api_key="k", model=LEGACY).model == LEGACY


def test_registry_has_no_thinking_level():
    schema = GoogleRealtimeLLMConfiguration.model_json_schema()["properties"]
    assert "thinking_level" not in schema
    assert "thinking_level" not in GoogleRealtimeLLMConfiguration.model_fields


def test_google_search_visible_for_supported_live_models():
    schema = GoogleRealtimeLLMConfiguration.model_json_schema()["properties"]
    assert schema["google_search"]["visible_for_models"] == [LEGACY, BASE]


# ------------------------------------------------------------------
# Factory
# ------------------------------------------------------------------


@pytest.mark.parametrize("model", [BASE, LEGACY])
def test_factory_passes_model_string(model):
    service = create_realtime_llm_service(
        DummyUserConfig(GoogleRealtimeLLMConfiguration(api_key="k", model=model)),
        _audio_config(),
    )
    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._settings.model == model
    assert service._google_search_enabled is False


def test_factory_passes_search_flag():
    service = create_realtime_llm_service(
        DummyUserConfig(
            GoogleRealtimeLLMConfiguration(api_key="k", model=BASE, google_search=True)
        ),
        _audio_config(),
    )
    assert service._google_search_enabled is True


# ------------------------------------------------------------------
# BLOCKING compatibility tagging
# ------------------------------------------------------------------


def test_base_38_tags_blocking_and_keeps_search_additive():
    tools = _tools(_make_service(BASE, google_search=True))
    declarations = tools[0]["function_declarations"]
    assert [d["name"] for d in declarations] == ["go_to_b"]
    assert declarations[0]["behavior"] == "BLOCKING"
    assert tools[-1] == {"google_search": {}}


def test_legacy_model_declarations_untagged():
    tools = _tools(_make_service(LEGACY))
    assert "behavior" not in tools[0]["function_declarations"][0]


def test_base_38_sends_no_thinking_config():
    # Upstream normalizes an absent ThinkingConfig to {} (falsy), which
    # _connect() maps back to "send nothing".
    assert not _make_service(BASE)._settings.thinking


# ------------------------------------------------------------------
# Transition policy (gemini-3.8-live only)
# ------------------------------------------------------------------

POLICY = DograhGeminiLiveLLMService.GEMINI_38_TRANSITION_POLICY


def _policy_service(model=BASE, **kwargs):
    service = _make_service(model, **kwargs)
    # Stub the reconnect hook: unit tests verify instruction composition,
    # not network connects.
    service._handle_changed_settings = AsyncMock(return_value=set())
    return service


async def _apply(service, prompt):
    from pipecat.services.settings import LLMSettings

    await service._update_settings(LLMSettings(system_instruction=prompt))
    return service._settings.system_instruction


def test_policy_only_mechanism_rule():
    assert "the ONLY mechanism" in POLICY


def test_policy_must_call_rule():
    assert "MUST call" in POLICY


def test_policy_no_simulate_next_node_rule():
    assert "Do not simulate the next node" in POLICY


def test_policy_no_next_node_information_rule():
    assert "collecting information that belongs to a later node" in POLICY


def test_policy_nonterminal_closing_guard():
    assert "Do not speak closing" in POLICY
    assert "only" in POLICY and "transition or end-call tool" in POLICY


def test_live_node_header_format():
    assert (
        live_node_header("Opening & Safety Check", "startCall")
        == "Current workflow node: Opening & Safety Check\nNode type: startCall"
    )


@pytest.mark.asyncio
async def test_policy_prepended_with_prompt_preserved():
    service = _policy_service()
    instruction = await _apply(
        service, "Current workflow node: Opening\n\nAsk for the name."
    )
    assert instruction.startswith(POLICY)
    assert instruction.endswith("Ask for the name.")
    assert "Current workflow node: Opening" in instruction


@pytest.mark.asyncio
async def test_policy_reapplies_to_new_node_prompt():
    service = _policy_service()
    await _apply(service, "Current workflow node: Opening\n\nAsk for the name.")
    instruction = await _apply(
        service, "Current workflow node: Caller Details\n\nAsk for the phone."
    )
    assert instruction.startswith(POLICY)
    assert "Caller Details" in instruction
    assert "Ask for the phone." in instruction
    assert "Opening" not in instruction


@pytest.mark.asyncio
async def test_policy_never_duplicated():
    service = _policy_service()
    prompt = "Current workflow node: Opening\n\nAsk for the name."
    await _apply(service, prompt)
    instruction = await _apply(service, prompt)
    assert instruction.count(POLICY) == 1


@pytest.mark.asyncio
async def test_legacy_model_gets_no_policy():
    service = _policy_service(LEGACY)
    assert service.supports_transition_policy is False
    prompt = "Ask for the name."
    assert await _apply(service, prompt) == prompt


def test_base_model_opts_into_policy():
    assert _policy_service(BASE).supports_transition_policy is True


def test_gpt_live_policy_untouched():
    from api.services.pipecat.realtime import openai_live

    assert "actually moves the workflow" in openai_live.TRANSITION_POLICY
    # The Gemini policy is adapted, not copied: it references the Live node
    # header rather than OpenAI delegation wording.
    assert "Current workflow node" in POLICY
    assert POLICY != openai_live.TRANSITION_POLICY


@pytest.mark.asyncio
async def test_base_38_connect_config_omits_thinking():
    # Exercise the real _connect/config seam: capture the LiveConnectConfig
    # at the network boundary instead of only asserting settings state.
    service = DograhGeminiLiveLLMService(
        api_key="test-key",
        google_search=True,
        settings=DograhGeminiLiveLLMService.Settings(
            model=BASE, voice="Puck", system_instruction="Test prompt."
        ),
    )
    captured = {}

    from pipecat.utils.asyncio.task_manager import TaskManager

    service._task_manager = TaskManager()

    async def fake_connect_handler(config=None, **kwargs):
        captured["config"] = config
        service._session = SimpleNamespace()
        return None

    service._connection_task_handler = fake_connect_handler
    # Production pre-populates the context (with node tools) before connect;
    # mirror that so the session payload includes workflow functions.
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.processors.aggregators.llm_context import LLMContext

    context = LLMContext()
    context.set_tools(
        ToolsSchema(
            standard_tools=[
                FunctionSchema(
                    name="go_to_b",
                    description="Transition.",
                    properties={},
                    required=[],
                )
            ]
        )
    )
    service._context = context
    await service._connect()
    await service._connection_task
    config = captured["config"]
    assert not getattr(config, "thinking_config", None)
    tools = list(config.tools or [])
    assert {"google_search": {}} in tools
    assert tools[0]["function_declarations"][0]["behavior"] == "BLOCKING"
