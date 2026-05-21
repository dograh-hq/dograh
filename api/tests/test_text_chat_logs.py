from api.services.workflow.text_chat_logs import (
    build_text_chat_realtime_feedback_events,
    visible_text_chat_turns,
)


def test_visible_text_chat_turns_trims_to_cursor_branch():
    session_data = {
        "cursor_turn_id": "turn-2",
        "turns": [
            {"id": "turn-1"},
            {"id": "turn-2"},
            {"id": "turn-3"},
        ],
    }

    assert visible_text_chat_turns(session_data) == [
        {"id": "turn-1"},
        {"id": "turn-2"},
    ]


def test_build_text_chat_realtime_feedback_events_uses_visible_branch_and_dedupes_node_transitions():
    session_data = {
        "cursor_turn_id": "turn-2",
        "turns": [
            {
                "id": "turn-1",
                "created_at": "2026-01-01T00:00:00+00:00",
                "events": [
                    {
                        "type": "node_transition",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "payload": {
                            "node_id": "node-start",
                            "node_name": "Start",
                            "previous_node_id": None,
                            "previous_node_name": None,
                            "allow_interrupt": False,
                        },
                    }
                ],
                "user_message": None,
                "assistant_message": {
                    "text": "Hello",
                    "created_at": "2026-01-01T00:00:01+00:00",
                },
            },
            {
                "id": "turn-2",
                "created_at": "2026-01-01T00:00:02+00:00",
                "events": [
                    {
                        "type": "node_transition",
                        "created_at": "2026-01-01T00:00:02+00:00",
                        "payload": {
                            "node_id": "node-start",
                            "node_name": "Start",
                            "previous_node_id": None,
                            "previous_node_name": None,
                            "allow_interrupt": False,
                        },
                    },
                    {
                        "type": "tool_call_started",
                        "created_at": "2026-01-01T00:00:03+00:00",
                        "payload": {
                            "function_name": "lookup_contact",
                            "tool_call_id": "tool-1",
                        },
                    },
                    {
                        "type": "tool_call_result",
                        "created_at": "2026-01-01T00:00:04+00:00",
                        "payload": {
                            "function_name": "lookup_contact",
                            "tool_call_id": "tool-1",
                            "result": {"contact_id": 42},
                        },
                    },
                ],
                "user_message": {
                    "text": "Find Abhishek",
                    "created_at": "2026-01-01T00:00:02+00:00",
                },
                "assistant_message": {
                    "text": "I found one match.",
                    "created_at": "2026-01-01T00:00:05+00:00",
                },
            },
            {
                "id": "turn-3",
                "created_at": "2026-01-01T00:00:06+00:00",
                "events": [
                    {
                        "type": "execution_error",
                        "created_at": "2026-01-01T00:00:06+00:00",
                        "payload": {"message": "Should be hidden after rewind"},
                    }
                ],
                "user_message": {
                    "text": "This turn is rewound away",
                    "created_at": "2026-01-01T00:00:06+00:00",
                },
                "assistant_message": None,
            },
        ],
    }

    events = build_text_chat_realtime_feedback_events(session_data)

    assert [event["type"] for event in events] == [
        "rtf-node-transition",
        "rtf-bot-text",
        "rtf-user-transcription",
        "rtf-function-call-start",
        "rtf-function-call-end",
        "rtf-bot-text",
    ]
    assert events[0]["payload"]["node_name"] == "Start"
    assert events[2]["payload"]["text"] == "Find Abhishek"
    assert events[4]["payload"]["result"] == "{'contact_id': 42}"
    assert all(
        event.get("payload", {}).get("error") != "Should be hidden after rewind"
        for event in events
    )
