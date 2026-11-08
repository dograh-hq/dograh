from copy import deepcopy
from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.workflow_configurations import (
    PromptVariablesConfiguration,
    WorkflowConfigurationDefaults,
)
from api.services.workflow.agent_handoff_context import build_handoff_snapshot
from api.services.workflow.conversation_history import (
    has_user_turns,
    normalize_conversation,
)
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)
from api.services.workflow.pipecat_engine_context_summarizer import (
    ContextSummarizationManager,
)
from api.services.workflow.prompt_variables import (
    DEFAULT_CALL_CONTEXT_NOTE,
    CallContextMessage,
    call_context_note,
    resolve_call_variables,
    update_call_context,
)
from api.utils.template_renderer import render_template


def test_inline_is_unchanged_and_call_context_is_static():
    node = SimpleNamespace(
        id="n",
        prompt="Hello {{name}}\\n{{missing | fallback:Unknown}}",
        add_global_prompt=True,
    )
    workflow = SimpleNamespace(
        global_node_id="global",
        nodes={
            "global": SimpleNamespace(id="global", prompt="Global {{name}}"),
            "n": node,
        },
    )

    def compose(name, config=None):
        return compose_system_prompt_for_node(
            node=node,
            workflow=workflow,
            has_recordings=False,
            format_prompt=lambda value: render_template(value, {"name": name}),
            prompt_variables=config,
        ).text

    assert compose("Ada") == "Global Ada\n\nHello Ada\nUnknown"
    config = PromptVariablesConfiguration(mode="call_context")
    assert compose("Ada", config) == compose("Bob", config)
    assert "{{missing}}" in compose("Ada", config)
    assert "Unknown" not in compose("Ada", config)


def test_complete_expressions_and_nested_paths_keep_renderer_semantics():
    template = "{{name | fallback:Alice}} {{name | fallback:Bob}} {{initial_context.city}} {{gathered_context.zip}} {{missing}} {{zero}} {{flag}} {{obj}}"
    context = {
        "city": "Rome",
        "gathered_context": {"zip": "00100"},
        "zero": 0,
        "flag": False,
        "obj": {"a": 1},
    }
    values = resolve_call_variables([("n", template)], context)
    assert values == {
        "name__fallback_1": "Alice",
        "name__fallback_2": "Bob",
        "initial_context.city": "Rome",
        "gathered_context.zip": "00100",
        "missing": "",
        "zero": "0",
        "flag": "False",
        "obj": '{"a": 1}',
    }


@pytest.mark.parametrize("fallback", ["Smith", "fallback:Smith"])
@pytest.mark.parametrize(
    "value,expected",
    [(None, "Smith"), ("", "Smith"), ("Lee", "Lee"), (0, "0"), (False, "False")],
)
def test_defaults_are_resolved_in_context_and_removed_from_prompt(
    fallback, value, expected
):
    node = SimpleNamespace(
        id="n",
        prompt="Name: {{first_name}} {{last_name}} {{middle_name | " + fallback + "}}",
        add_global_prompt=False,
    )
    workflow = SimpleNamespace(global_node_id=None, nodes={"n": node})
    context = {"first_name": "Ada", "last_name": "Jones", "middle_name": value}
    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=workflow,
        format_prompt=lambda text: render_template(text, context),
        has_recordings=False,
        prompt_variables=PromptVariablesConfiguration(mode="call_context"),
    ).text
    values = resolve_call_variables([("n", node.prompt)], context)
    assert prompt.startswith("Name: {{first_name}} {{last_name}} {{middle_name}}\n\n")
    assert "Smith" not in prompt and " | " not in prompt
    assert values == {
        "first_name": "Ada",
        "last_name": "Jones",
        "middle_name": expected,
    }
    assert render_template(prompt.split("\n\n")[0], values) == render_template(
        node.prompt, context
    )
    assert CallContextMessage(values)["content"] == (
        '<call_context>\nfirst_name: "Ada"\nlast_name: "Jones"\n'
        f'middle_name: "{expected}"\n</call_context>'
    )


def test_conflicting_defaults_keep_distinct_names_across_global_and_node_prompts():
    global_node = SimpleNamespace(
        id="global", prompt="{{name}} {{name | Alice}} {{name__fallback_1}}"
    )
    node = SimpleNamespace(
        id="n",
        prompt="{{name | fallback:Bob}} {{name | Alice}}",
        add_global_prompt=True,
    )
    nodes = {"global": global_node, "n": node}
    templates = [(key, node.prompt) for key, node in nodes.items()]
    context = {"name__fallback_1": "Original variable"}
    values = resolve_call_variables(templates, context)
    assert values == {
        "name": "",
        "name__fallback_2": "Alice",
        "name__fallback_1": "Original variable",
        "name__fallback_3": "Bob",
    }
    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=SimpleNamespace(global_node_id="global", nodes=nodes),
        format_prompt=lambda text: text,
        has_recordings=False,
        prompt_variables=PromptVariablesConfiguration(mode="call_context"),
    ).text
    assert " | " not in prompt
    assert "Alice" not in prompt and "Bob" not in prompt
    assert render_template(prompt.split("\n\n## CALL VARIABLES")[0], values) == (
        render_template(global_node.prompt, context)
        + "\n\n"
        + render_template(node.prompt, context)
    )
    assert resolve_call_variables(list(reversed(templates)), context) == values
    updated = resolve_call_variables(templates, {**context, "name": "Ada"})
    assert set(updated) == set(values)
    assert (
        updated["name"]
        == updated["name__fallback_2"]
        == updated["name__fallback_3"]
        == "Ada"
    )


def test_metadata_updates_do_not_become_caller_speech():
    messages = [{"role": "assistant", "content": "Hello"}]
    update_call_context(messages, {"name": "Ada", "city": ""})
    assert isinstance(messages[0], CallContextMessage)
    update_call_context(messages, {"name": "Ada", "city": ""})
    assert len(messages) == 2
    update_call_context(messages, {"name": "Ada", "city": "Rome"})
    assert messages[-1].values == {"city": "Rome"}
    context = LLMContext(deepcopy(messages))
    assert not has_user_turns(context)
    assert normalize_conversation(context) == [
        {"role": "assistant", "content": "Hello"}
    ]
    assert (
        "Rome" not in build_handoff_snapshot(context, source_agent_name="A").transcript
    )
    context.add_message({"role": "user", "content": "<call_context> real speech"})
    assert has_user_turns(context)
    assert len(normalize_conversation(context)) == 2


@pytest.mark.parametrize("mode", ["inline", "call_context"])
@pytest.mark.parametrize("explicit", [True, False])
def test_layout_is_the_only_workflow_cache_policy(mode, explicit):
    assert WorkflowConfigurationDefaults().prompt_variables.mode == "inline"
    configs = WorkflowConfigurationDefaults.model_validate(
        {
            "prompt_variables": {"mode": mode},
            "prompt_caching": {"explicit": explicit},
        }
    )
    assert configs.prompt_variables.mode == mode
    assert "prompt_caching" not in configs.model_dump()
    assert (
        "prompt_caching"
        not in WorkflowConfigurationDefaults.model_json_schema()["properties"]
    )


def test_variable_update_does_not_split_tool_call_and_result():
    messages = [
        CallContextMessage({"city": ""}),
        {
            "role": "assistant",
            "tool_calls": [{"id": "one", "function": {"name": "next"}}],
        },
    ]
    update_call_context(messages, {"city": "Rome"})
    messages.append({"role": "tool", "tool_call_id": "one", "content": "done"})
    assert isinstance(messages[1], CallContextMessage)
    assert messages[-2]["role"] == "assistant" and messages[-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_answer_gate_does_not_discard_call_metadata():
    from pipecat.frames.frames import LLMContextFrame
    from pipecat.processors.frame_processor import FrameDirection

    from api.services.pipecat.processors.answer_supervisor import AnswerContextGate

    message = CallContextMessage({"name": "Ada"})
    speech = {"role": "user", "content": "Hello"}
    gate = AnswerContextGate()
    await gate.process_frame(
        LLMContextFrame(LLMContext([message, speech])), FrameDirection.DOWNSTREAM
    )
    assert id(message) not in gate.held_user_messages
    assert id(speech) in gate.held_user_messages


@pytest.mark.asyncio
async def test_compaction_pins_metadata_and_preserves_concurrent_updates():
    messages = [CallContextMessage({"city": "", "name": "Ada"})] + [
        {"role": "user", "content": str(i)} for i in range(8)
    ]
    context = LLMContext(messages)

    async def summarize(frame):
        assert not any(
            isinstance(m, CallContextMessage) for m in frame.context.messages
        )
        context.add_message(CallContextMessage({"city": "Rome"}))
        context.add_message({"role": "user", "content": "new speech"})
        return "summary", 5

    llm = SimpleNamespace(_generate_summary=summarize, model_name="test")
    engine = SimpleNamespace(
        context=context,
        active_agent=SimpleNamespace(
            inference_llm=llm, current_node=SimpleNamespace(id="n")
        ),
        _get_otel_context=lambda: None,
    )
    await ContextSummarizationManager(engine)._summarize_context_in_background()
    assert context.messages[0].values == {"city": "Rome", "name": "Ada"}
    assert sum(isinstance(m, CallContextMessage) for m in context.messages) == 1
    assert context.messages[-1]["content"] == "new speech"


@pytest.mark.parametrize("note", [None, "", " \n\t"])
def test_blank_call_context_note_uses_default(note):
    assert (
        call_context_note(PromptVariablesConfiguration(call_context_note=note))
        == DEFAULT_CALL_CONTEXT_NOTE
    )


def test_no_metadata_message_when_there_are_no_variables():
    messages = [{"role": "assistant", "content": "Hello"}]
    update_call_context(messages, {})
    assert len(messages) == 1


def test_text_chat_checkpoint_preserves_metadata_identity():
    from api.services.workflow.text_chat_runner import (
        _deserialize_text_chat_checkpoint_messages,
        _serialize_text_chat_checkpoint_messages,
    )

    messages = [
        CallContextMessage({"name": "Ada"}),
        {"role": "user", "content": "hello"},
    ]
    restored = _deserialize_text_chat_checkpoint_messages(
        _serialize_text_chat_checkpoint_messages(messages)
    )
    assert isinstance(restored[0], CallContextMessage)
    assert restored[0].values == {"name": "Ada"}
    assert normalize_conversation(LLMContext(restored)) == [messages[1]]


def test_conflicting_bare_timezones_have_static_source_labels():
    a = SimpleNamespace(
        id="a",
        prompt="{{current_time}} {{current_time_Europe/Rome}}",
        add_global_prompt=False,
    )
    b = SimpleNamespace(
        id="b",
        prompt="{{current_time}} {{current_time_America/New_York}}",
        add_global_prompt=False,
    )
    workflow = SimpleNamespace(global_node_id=None, nodes={"a": a, "b": b})
    config = PromptVariablesConfiguration(mode="call_context")
    prompt = compose_system_prompt_for_node(
        node=a,
        workflow=workflow,
        format_prompt=lambda text: text,
        has_recordings=False,
        prompt_variables=config,
    ).text
    values = resolve_call_variables([("a", a.prompt), ("b", b.prompt)], {})
    assert "Call-variable source: node a, timezone Europe/Rome" in prompt
    assert "{{current_time}}" in prompt
    assert "current_time (node a, timezone Europe/Rome)" in values
    assert "current_time (node b, timezone America/New_York)" in values


@pytest.mark.asyncio
async def test_engine_updates_values_after_extraction_without_changing_system(
    three_node_workflow,
):
    from unittest.mock import AsyncMock

    from api.services.workflow.pipecat_engine import PipecatEngine
    from pipecat.tests import MockLLMService

    node = three_node_workflow.nodes[three_node_workflow.start_node_id]
    node.prompt = (
        "Hello {{customer_name}}; city {{gathered_context.city | Unknown city}}"
    )
    llm = MockLLMService(mock_chunks=[])
    engine = PipecatEngine(
        llm=llm,
        context=LLMContext([{"role": "assistant", "content": "Greeting"}]),
        workflow=three_node_workflow,
        call_context_vars={"customer_name": "Ada"},
        workflow_run_id=1,
        prompt_variables=PromptVariablesConfiguration(mode="call_context"),
    )
    engine._get_organization_id = AsyncMock(return_value=1)
    await engine._setup_llm_context(node)
    prompt = engine.active_agent.system_prompt
    assert "{{customer_name}}" in prompt and "Ada" not in prompt
    assert engine.context.messages[0].values["customer_name"] == "Ada"
    assert "{{gathered_context.city}}" in prompt and "Unknown city" not in prompt
    assert engine.context.messages[0].values["gathered_context.city"] == "Unknown city"
    engine.record_context({"city": "Rome"})
    assert engine.context.messages[-1].values == {"gathered_context.city": "Rome"}
    assert engine.active_agent.system_prompt == prompt
    assert normalize_conversation(engine.context) == [
        {"role": "assistant", "content": "Greeting"}
    ]
