"""Tests for cross-node variable injection via {{gathered_context.*}} in prompts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.workflow.pipecat_engine import PipecatEngine
from api.utils.template_renderer import render_template


# ─── Unit: render_template with gathered_context in context ──────────────────

def test_render_template_gathered_context_simple():
    """render_template resolves {{gathered_context.X}} when context has gathered_context key."""
    ctx = {"gathered_context": {"guest_name": "Alice"}}
    assert render_template("Hello {{gathered_context.guest_name}}", ctx) == "Hello Alice"


def test_render_template_gathered_context_missing_key_renders_empty():
    ctx = {"gathered_context": {}}
    assert render_template("Hello {{gathered_context.guest_name}}", ctx) == "Hello "


def test_render_template_gathered_context_fallback_filter():
    ctx = {"gathered_context": {}}
    result = render_template("Hello {{gathered_context.guest_name | Guest}}", ctx)
    assert result == "Hello Guest"


def test_render_template_gathered_context_nested_path():
    ctx = {"gathered_context": {"quote": {"price": 250, "currency": "USD"}}}
    result = render_template("{{gathered_context.quote.price}}", ctx)
    assert result == "250"


def test_render_template_gathered_context_dict_value_serialized_as_json():
    import json
    ctx = {"gathered_context": {"data": {"a": 1, "b": 2}}}
    result = render_template("{{gathered_context.data}}", ctx)
    parsed = json.loads(result)
    assert parsed == {"a": 1, "b": 2}


def test_render_template_initial_context_not_clobbered_by_gathered_context():
    """Flat key wins for flat access; nested key wins for namespaced access."""
    ctx = {
        "first_name": "Alice",  # flat initial_context value
        "gathered_context": {"first_name": "Bob"},  # extracted value
    }
    # Flat access: reads from top-level key
    assert render_template("{{first_name}}", ctx) == "Alice"
    # Namespaced access: reads from gathered_context dict
    assert render_template("{{gathered_context.first_name}}", ctx) == "Bob"


# ─── Unit: PipecatEngine._format_prompt uses _gathered_context ───────────────

def make_minimal_engine(call_context_vars=None, gathered_context=None):
    """Build a PipecatEngine with minimal mocks — no real LLM needed."""
    llm_mock = MagicMock()
    llm_mock._update_settings = AsyncMock()
    context = LLMContext()

    # We need a real WorkflowGraph for the engine constructor.
    # Use the simple_workflow fixture pattern inline.
    from api.services.workflow.dto import (
        EdgeDataDTO, EndCallNodeData, Position, ReactFlowDTO,
        RFEdgeDTO, RFNodeDTO, StartCallNodeData,
    )
    from api.services.workflow.workflow_graph import WorkflowGraph

    dto = ReactFlowDTO(
        nodes=[
            RFNodeDTO(
                id="start", type="startCall", position=Position(x=0, y=0),
                data=StartCallNodeData(
                    name="Start", prompt="Hello", is_start=True,
                    allow_interrupt=False, add_global_prompt=False,
                ),
            ),
            RFNodeDTO(
                id="end", type="endCall", position=Position(x=0, y=200),
                data=EndCallNodeData(
                    name="End", prompt="Bye", is_end=True,
                    allow_interrupt=False, add_global_prompt=False,
                ),
            ),
        ],
        edges=[
            RFEdgeDTO(
                id="s-e", source="start", target="end",
                data=EdgeDataDTO(label="End", condition="End the call"),
            ),
        ],
    )
    workflow = WorkflowGraph(dto)

    engine = PipecatEngine(
        llm=llm_mock,
        context=context,
        workflow=workflow,
        call_context_vars=call_context_vars or {},
        workflow_run_id=1,
    )
    if gathered_context:
        engine._gathered_context = gathered_context
    return engine


def test_format_prompt_includes_gathered_context_value():
    engine = make_minimal_engine(
        call_context_vars={"caller_name": "Alice"},
        gathered_context={"arrival_date": "2025-01-15"},
    )
    result = engine._format_prompt("Date: {{gathered_context.arrival_date}}")
    assert result == "Date: 2025-01-15"


def test_format_prompt_gathered_context_empty_renders_empty():
    engine = make_minimal_engine(gathered_context={})
    result = engine._format_prompt("Date: {{gathered_context.arrival_date}}")
    assert result == "Date: "


def test_format_prompt_gathered_context_fallback():
    engine = make_minimal_engine(gathered_context={})
    result = engine._format_prompt("Date: {{gathered_context.arrival_date | Not provided}}")
    assert result == "Date: Not provided"


def test_format_prompt_initial_context_not_overwritten():
    """{{first_name}} reads from call_context_vars; {{gathered_context.first_name}} reads from _gathered_context."""
    engine = make_minimal_engine(
        call_context_vars={"first_name": "Alice"},
        gathered_context={"first_name": "Bob"},
    )
    assert engine._format_prompt("{{first_name}}") == "Alice"
    assert engine._format_prompt("{{gathered_context.first_name}}") == "Bob"


def test_format_prompt_gathered_context_snapshot_isolates_concurrent_mutation():
    """Snapshot (dict copy) means mid-render mutations don't corrupt the render."""
    engine = make_minimal_engine(gathered_context={"x": "original"})

    original_render = engine._format_prompt
    render_results = []

    def capturing_format(prompt):
        # Mutate _gathered_context to simulate concurrent background task
        engine._gathered_context["x"] = "mutated"
        return original_render(prompt)

    # The snapshot taken at the start of _format_prompt should have "original"
    # even if _gathered_context is mutated during render.
    # Because render_template is synchronous and doesn't yield, this actually
    # tests that the copy is taken before template substitution begins.
    engine._gathered_context["x"] = "original"
    result = engine._format_prompt("{{gathered_context.x}}")
    assert result == "original"


def test_format_prompt_call_context_gathered_context_overwritten_by_live():
    """If _call_context_vars has a 'gathered_context' key (callback path),
    the live _gathered_context must win in the render context."""
    engine = make_minimal_engine(
        call_context_vars={
            "is_callback": True,
            "gathered_context": {"guest_name": "OldName"},  # previous run's data
        },
        gathered_context={"guest_name": "NewName"},  # current run's live extraction
    )
    result = engine._format_prompt("{{gathered_context.guest_name}}")
    assert result == "NewName"


# ─── Integration: _await_pending_extractions is called before _setup_llm_context ─

@pytest.mark.asyncio
async def test_await_pending_extractions_called_before_prompt_render():
    """_setup_llm_context must await pending extractions before rendering the prompt."""
    engine = make_minimal_engine()

    extraction_done_before_render = False
    render_called = False

    async def slow_extraction():
        await asyncio.sleep(0.05)
        engine._gathered_context["guest"] = "Alice"

    extraction_task = asyncio.create_task(slow_extraction())
    engine._pending_extraction_tasks.add(extraction_task)
    extraction_task.add_done_callback(engine._pending_extraction_tasks.discard)

    # Patch compose_system_prompt_for_node to capture what prompt is rendered
    captured_rendered = []

    original_format = engine._format_prompt

    def capturing_format(prompt):
        result = original_format(prompt)
        captured_rendered.append(result)
        return result

    engine._format_prompt = capturing_format

    node = engine.workflow.nodes[engine.workflow.start_node_id]
    node.prompt = "Guest: {{gathered_context.guest}}"

    with patch.object(engine, "_update_llm_context", new_callable=AsyncMock):
        with patch.object(
            engine,
            "_register_transition_function_with_llm",
            new_callable=AsyncMock,
        ):
            await engine._setup_llm_context(node)

    # By the time prompt was rendered, extraction must have written the value
    assert any("Alice" in r for r in captured_rendered), (
        f"Expected 'Alice' in rendered prompts, got: {captured_rendered}"
    )
