from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_function_call_end_event,
    build_node_transition_event,
    realtime_feedback_event_sort_key,
    stamp_realtime_feedback_event,
)


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
        timestamp="2026-01-01T00:00:03+00:00",
        turn=0,
        node_id="node-1",
        node_name="Greeting",
    )
    bot_text = stamp_realtime_feedback_event(
        build_bot_text_event(
            text="Hello there",
            timestamp="2026-01-01T00:00:01+00:00",
        ),
        timestamp="2026-01-01T00:00:02+00:00",
        turn=0,
    )

    events = sorted([node_transition, bot_text], key=realtime_feedback_event_sort_key)

    assert events == [bot_text, node_transition]
    assert node_transition["node_id"] == "node-1"
    assert node_transition["node_name"] == "Greeting"
