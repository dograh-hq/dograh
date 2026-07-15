from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_function_call_end_event,
    build_node_transition_event,
    build_user_transcription_event,
    realtime_feedback_event_sort_key,
    stamp_realtime_feedback_event,
)
from api.utils.transcript import generate_transcript_text


def test_build_function_call_end_event_serializes_results():
    event = build_function_call_end_event(
        function_name="lookup_contact",
        tool_call_id="tool-1",
        result={"contact_id": 42},
    )

    assert event == {
        "type": "rtf-function-call-end",
        "payload": {
            "function_name": "lookup_contact",
            "tool_call_id": "tool-1",
            "result": "{'contact_id': 42}",
        },
    }


def test_stamp_and_sort_realtime_feedback_events():
    node_transition = stamp_realtime_feedback_event(
        build_node_transition_event(
            node_id="node-1",
            node_name="Greeting",
            previous_node_id=None,
            previous_node_name=None,
        ),
        timestamp="2026-01-01T00:00:01+00:00",
        turn=0,
        node_id="node-1",
        node_name="Greeting",
    )
    bot_text = stamp_realtime_feedback_event(
        build_bot_text_event(
            text="Hello there",
            # Deliberately earlier than the node's event timestamp: ordering
            # follows the top-level event timestamp, not payload speech time.
            timestamp="2026-01-01T00:00:00+00:00",
        ),
        timestamp="2026-01-01T00:00:02+00:00",
        turn=0,
    )

    events = sorted([node_transition, bot_text], key=realtime_feedback_event_sort_key)

    assert events == [node_transition, bot_text]
    assert node_transition["node_id"] == "node-1"
    assert node_transition["node_name"] == "Greeting"


def test_transcript_can_include_end_timestamps_without_changing_default_format():
    events = [
        stamp_realtime_feedback_event(
            build_bot_text_event(
                text="Can you confirm your date of birth?",
                timestamp="2026-01-01T00:00:01+00:00",
                end_timestamp="2026-01-01T00:00:04+00:00",
            ),
            timestamp="2026-01-01T00:00:05+00:00",
            turn=0,
        ),
        stamp_realtime_feedback_event(
            build_user_transcription_event(
                text="January fifth",
                final=True,
                timestamp="2026-01-01T00:00:06+00:00",
                end_timestamp="2026-01-01T00:00:08+00:00",
            ),
            timestamp="2026-01-01T00:00:09+00:00",
            turn=1,
        ),
    ]

    assert generate_transcript_text(events) == (
        "[2026-01-01T00:00:01+00:00] assistant: Can you confirm your date of birth?\n"
        "[2026-01-01T00:00:06+00:00] user: January fifth\n"
    )
    assert generate_transcript_text(events, include_end_timestamps=True) == (
        "[2026-01-01T00:00:01+00:00 -> 2026-01-01T00:00:04+00:00] "
        "assistant: Can you confirm your date of birth?\n"
        "[2026-01-01T00:00:06+00:00 -> 2026-01-01T00:00:08+00:00] "
        "user: January fifth\n"
    )
