from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_function_call_end_event,
    build_function_call_start_event,
    build_ttfb_metric_event,
    build_user_transcription_event,
)
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
    assert result["ttft_seconds"] is not None


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


def _reconstruct_events(events):
    return reconstruct_replay_turns(
        SimpleNamespace(
            id=1,
            created_at=datetime(2026, 11, 7, tzinfo=UTC),
            initial_context={},
            logs={"realtime_feedback_events": events},
        ),
        SimpleNamespace(start_node_id="n", nodes={"n": object(), "next": object()}),
    )


def _llm_boundary():
    return build_ttfb_metric_event(
        ttfb_seconds=0.2, processor="GoogleLLMService", model="test", kind="llm"
    )


def test_reconstruction_advances_clock_between_same_node_requests():
    items = _reconstruct_events(
        [
            {
                "type": "rtf-user-transcription",
                "timestamp": "2026-11-07T10:00:00Z",
                "payload": {"text": "Hello"},
            },
            build_bot_text_event(text="Hi"),
            {
                "type": "rtf-user-transcription",
                "timestamp": "2026-11-07T11:00:00Z",
                "payload": {"text": "Now?"},
            },
            build_bot_text_event(text="Yes"),
        ]
    )
    assert items[0]["clock"].hour == 10
    assert items[1]["clock"].hour == 11


@pytest.mark.parametrize("boundaries", ["timing", "groups", "legacy"])
def test_reconstruction_splits_tool_round_trips_and_replays_recorded_results(
    boundaries,
):
    events = [build_user_transcription_event(text="Cancel my booking", final=True)]
    for index, name in enumerate(("lookup", "cancel")):
        if boundaries == "timing":
            events.append(_llm_boundary())
        events.extend(
            [
                build_function_call_start_event(
                    function_name=name,
                    tool_call_id=f"tool-{index}",
                    arguments={"booking": 42},
                    group_id=f"group-{index}" if boundaries == "groups" else None,
                ),
                build_function_call_end_event(
                    function_name=name,
                    tool_call_id=f"tool-{index}",
                    result={"ok": True},
                ),
            ]
        )
        if index == 0:
            events.append(
                {"type": "rtf-node-transition", "payload": {"node_id": "next"}}
            )
    if boundaries == "timing":
        events.append(_llm_boundary())
    events.append(build_bot_text_event(text="Cancelled."))
    items = _reconstruct_events(events)
    assert len(items) == 3
    assert [item["node_id"] for item in items] == ["n", "next", "next"]
    assert items[0]["expected"]["tool_calls"] == [
        {"name": "lookup", "arguments": {"booking": 42}}
    ]
    assert items[1]["expected"]["tool_calls"] == [
        {"name": "cancel", "arguments": {"booking": 42}}
    ]
    assert items[2]["expected"] == {"text": "Cancelled.", "tool_calls": []}
    assert items[0]["history"] == [{"role": "user", "content": "Cancel my booking"}]
    assert items[1]["history"][-1] == {
        "role": "tool",
        "tool_call_id": "tool-0",
        "content": '{"ok": true}',
    }
    assert items[2]["history"][-1]["tool_call_id"] == "tool-1"
    assert "Cancelled." not in str(items[2]["history"])


@pytest.mark.parametrize("boundaries", ["timing", "groups"])
def test_reconstruction_keeps_sequential_execution_of_one_tool_group_together(
    boundaries,
):
    events = [build_user_transcription_event(text="Check both", final=True)]
    if boundaries == "timing":
        events.append(_llm_boundary())
    for name in ("first", "second"):
        events.extend(
            [
                build_function_call_start_event(
                    function_name=name,
                    tool_call_id=name,
                    arguments={},
                    group_id="same-request" if boundaries == "groups" else None,
                ),
                build_function_call_end_event(
                    function_name=name, tool_call_id=name, result={"ok": True}
                ),
            ]
        )
    if boundaries == "timing":
        events.append(_llm_boundary())
    events.append(build_bot_text_event(text="Both checked."))
    items = _reconstruct_events(events)
    assert len(items) == 2
    assert [call["name"] for call in items[0]["expected"]["tool_calls"]] == [
        "first",
        "second",
    ]
    history = items[1]["history"]
    assert [message["role"] for message in history] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert [call["id"] for call in history[1]["tool_calls"]] == ["first", "second"]
    assert [message["tool_call_id"] for message in history[2:]] == ["first", "second"]
    assert len(items[0]["history"]) == 1


@pytest.mark.parametrize("boundary", ["timing", "groups", "user"])
@pytest.mark.parametrize("completed_sibling", [False, True])
def test_reconstruction_recovers_after_missing_tool_result(boundary, completed_sibling):
    events = [build_user_transcription_event(text="Check my booking", final=True)]
    if completed_sibling:
        events.append(
            build_function_call_start_event(
                function_name="lookup",
                tool_call_id="done",
                group_id="first" if boundary == "groups" else None,
            )
        )
    events.append(
        build_function_call_start_event(
            function_name="slow_lookup",
            tool_call_id="orphan",
            group_id="first" if boundary == "groups" else None,
        )
    )
    if completed_sibling:
        events.append(
            build_function_call_end_event(
                function_name="lookup", tool_call_id="done", result={"found": True}
            )
        )
    if boundary == "timing":
        events.append(_llm_boundary())
    elif boundary == "user":
        events.append(build_user_transcription_event(text="Try again", final=True))
    events.extend(
        [
            build_function_call_start_event(
                function_name="retry",
                tool_call_id="retry",
                group_id="second" if boundary == "groups" else None,
            ),
            # A late result for the abandoned request must not become an
            # unmatched tool response in the reconstructed conversation.
            build_function_call_end_event(
                function_name="slow_lookup", tool_call_id="orphan", result="late"
            ),
            build_function_call_end_event(
                function_name="retry", tool_call_id="retry", result={"found": True}
            ),
            build_bot_text_event(text="Found it."),
        ]
    )
    items = _reconstruct_events(events)
    assert len(items) == 3
    assert items[1]["expected"]["tool_calls"] == [{"name": "retry", "arguments": {}}]
    assert items[2]["expected"]["text"] == "Found it."
    for item in items[1:]:
        calls = [
            call["id"]
            for message in item["history"]
            for call in message.get("tool_calls", [])
        ]
        results = [
            message["tool_call_id"]
            for message in item["history"]
            if message["role"] == "tool"
        ]
        assert "orphan" not in calls
        assert ("done" in calls) == completed_sibling
        assert calls == results
        assert all(
            message.get("content") or message.get("tool_calls")
            for message in item["history"]
        )


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
