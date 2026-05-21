import pytest

from api.services.workflow.text_chat_session_service import (
    TextChatTurnNotFoundError,
    build_pending_text_chat_turn,
    truncate_text_chat_future_turns,
    validate_text_chat_turn_cursor,
)


def test_build_pending_text_chat_turn_sets_pending_shape():
    turn = build_pending_text_chat_turn(user_text="Hello")

    assert turn["id"].startswith("turn_")
    assert turn["status"] == "pending"
    assert turn["user_message"]["text"] == "Hello"
    assert turn["assistant_message"] is None
    assert turn["events"] == []
    assert turn["usage"] == {}


def test_truncate_text_chat_future_turns_moves_rewound_branch_to_discarded_future():
    session_data = {
        "cursor_turn_id": "turn-2",
        "turns": [
            {"id": "turn-1"},
            {"id": "turn-2"},
            {"id": "turn-3"},
        ],
        "discarded_future": [],
    }

    active_turns, discarded_future = truncate_text_chat_future_turns(session_data)

    assert active_turns == [{"id": "turn-1"}, {"id": "turn-2"}]
    assert discarded_future[0]["rewound_from_turn_id"] == "turn-2"
    assert discarded_future[0]["turns"] == [{"id": "turn-3"}]


def test_validate_text_chat_turn_cursor_raises_for_missing_turn():
    with pytest.raises(TextChatTurnNotFoundError):
        validate_text_chat_turn_cursor(
            {"turns": [{"id": "turn-1"}]},
            "turn-404",
        )
