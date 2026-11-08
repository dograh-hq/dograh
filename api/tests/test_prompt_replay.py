from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.workflow.prompt_replay import (
    reconstruct_replay_turns,
    replay_sample_metrics,
    run_replay_inference,
)
from api.services.workflow.prompt_variables import resolve_call_variables
from api.utils.template_renderer import render_template, template_clock
from pipecat.tests import MockLLMService


@pytest.mark.asyncio
async def test_replay_captures_text_through_real_pipeline():
    llm = MockLLMService(
        mock_chunks=MockLLMService.create_text_chunks("Hello Ada"), chunk_delay=0
    )
    result = await run_replay_inference(
        llm, LLMContext([{"role": "user", "content": "Hello"}])
    )
    assert result["text"] == "Hello Ada"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_replay_observes_tools_without_executing_them():
    llm = MockLLMService(
        mock_chunks=MockLLMService.create_function_call_chunks(
            function_name="charge_customer",
            arguments={"amount": 100},
            tool_call_id="call-1",
        ),
        chunk_delay=0,
    )
    result = await run_replay_inference(
        llm, LLMContext([{"role": "user", "content": "Pay"}])
    )
    assert result["tool_calls"] == [
        {"name": "charge_customer", "arguments": {"amount": 100}}
    ]


def test_reconstruction_excludes_future_context_and_keeps_greeting():
    run = SimpleNamespace(
        id=1,
        created_at=datetime(2026, 11, 7, tzinfo=UTC),
        initial_context={"name": "Ada"},
        gathered_context={"outcome": "future"},
        logs={
            "realtime_feedback_events": [
                {"type": "rtf-bot-text", "payload": {"text": "Greeting"}},
                {
                    "type": "rtf-user-transcription",
                    "payload": {"text": "Hello", "final": True},
                },
                {"type": "rtf-bot-text", "payload": {"text": "How can I help?"}},
                {
                    "type": "rtf-user-transcription",
                    "payload": {"text": "Bye", "final": True},
                },
                {"type": "rtf-bot-text", "payload": {"text": "Goodbye"}},
            ]
        },
    )
    graph = SimpleNamespace(start_node_id="n", nodes={"n": object()})
    items = reconstruct_replay_turns(run, graph)
    assert len(items) == 2
    assert items[0]["history"] == [
        {"role": "assistant", "content": "Greeting"},
        {"role": "user", "content": "Hello"},
    ]
    assert "outcome" not in items[0]["initial"]
    assert items[1]["expected"]["text"] == "Goodbye"


def test_replay_clock_preserves_timezone_and_weekday():
    template = "{{current_time_Europe/Rome}} {{current_weekday}}"
    with template_clock(datetime(2026, 11, 6, 23, 30, tzinfo=UTC)):
        rendered = render_template(template, {})
        values = resolve_call_variables([("n", template)], {})
    assert rendered == "2026-11-07 00:30:00 CET Saturday"
    assert values["current_weekday_Europe/Rome"] == "Saturday"
    assert values["current_weekday"] == "Saturday"


def test_sample_metrics_use_gross_input_and_exclude_failed_requests():
    good = {
        "text": "Hello",
        "tool_calls": [],
        "error": None,
        "ttft_seconds": 0.8,
        "usage": {
            "prompt_tokens": 100,
            "cache_read_input_tokens": 900,
            "total_tokens": 1050,
            "completion_tokens": 50,
        },
    }
    failed = {**good, "usage": {}, "error": "429"}
    metrics = replay_sample_metrics(
        [
            {
                "inline": [good, failed],
                "call_context": [good, good],
                "expected": {"tool_calls": []},
            }
        ]
    )
    assert metrics["A1"]["cached_share"] == 0.9
    assert metrics["A1"]["production_action_agreement"] == 1
    assert metrics["A2"]["failed_requests"] == 1
    assert metrics["A2"]["cached_share"] is None
