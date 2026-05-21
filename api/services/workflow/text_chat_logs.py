"""Helpers for projecting text-chat session state into run-log snapshots."""

from typing import Any

from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_function_call_end_event,
    build_function_call_start_event,
    build_node_transition_event,
    build_pipeline_error_event,
    build_user_transcription_event,
    realtime_feedback_event_sort_key,
    stamp_realtime_feedback_event,
)


def visible_text_chat_turns(session_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the active branch of turns for the current text-chat session.

    After a rewind, `session_data["turns"]` may still contain future turns until
    the next message is sent. Those turns are no longer part of the visible
    branch, so callers that synthesize transcript/log views should trim at
    `cursor_turn_id`.
    """
    turns = list(session_data.get("turns") or [])
    cursor_turn_id = session_data.get("cursor_turn_id")
    if cursor_turn_id is None:
        return turns

    for index, turn in enumerate(turns):
        if turn.get("id") == cursor_turn_id:
            return turns[: index + 1]

    return turns


def build_text_chat_realtime_feedback_events(
    session_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project text-chat session state into `workflow_runs.logs` event format.

    `workflow_run_text_sessions` holds the authoritative rewindable conversation
    state. Historical run pages and QA helpers read the normalized
    `workflow_runs.logs.realtime_feedback_events` schema instead, so this helper
    rebuilds that snapshot from the currently visible branch.
    """
    events: list[dict[str, Any]] = []
    last_emitted_node_id: str | None = None

    for turn_index, turn in enumerate(visible_text_chat_turns(session_data)):
        turn_events = list(turn.get("events") or [])
        for event in turn_events:
            payload = dict(event.get("payload") or {})
            event_type = event.get("type")
            timestamp = event.get("created_at") or turn.get("created_at")

            if event_type == "node_transition":
                node_id = payload.get("node_id")
                if node_id is not None and node_id == last_emitted_node_id:
                    continue
                snapshot_event = stamp_realtime_feedback_event(
                    build_node_transition_event(
                        node_id=node_id,
                        node_name=payload.get("node_name"),
                        previous_node_id=payload.get("previous_node_id"),
                        previous_node_name=payload.get("previous_node_name"),
                        allow_interrupt=bool(payload.get("allow_interrupt", False)),
                    ),
                    timestamp=timestamp,
                    turn=turn_index,
                    node_id=node_id,
                    node_name=payload.get("node_name"),
                )
                if node_id is not None:
                    last_emitted_node_id = node_id
                events.append(snapshot_event)
            elif event_type == "tool_call_started":
                events.append(
                    stamp_realtime_feedback_event(
                        build_function_call_start_event(
                            function_name=payload.get("function_name"),
                            tool_call_id=payload.get("tool_call_id"),
                            arguments=payload.get("arguments"),
                        ),
                        timestamp=timestamp,
                        turn=turn_index,
                    )
                )
            elif event_type == "tool_call_result":
                events.append(
                    stamp_realtime_feedback_event(
                        build_function_call_end_event(
                            function_name=payload.get("function_name"),
                            tool_call_id=payload.get("tool_call_id"),
                            result=payload.get("result"),
                        ),
                        timestamp=timestamp,
                        turn=turn_index,
                    )
                )
            elif event_type == "execution_error":
                events.append(
                    stamp_realtime_feedback_event(
                        build_pipeline_error_event(
                            error=payload.get("message", "Execution error"),
                            fatal=True,
                        ),
                        timestamp=timestamp,
                        turn=turn_index,
                    )
                )

        user_message = turn.get("user_message") or {}
        if user_message.get("text"):
            message_timestamp = user_message.get("created_at") or turn.get("created_at")
            events.append(
                stamp_realtime_feedback_event(
                    build_user_transcription_event(
                        text=user_message["text"],
                        final=True,
                        timestamp=message_timestamp,
                    ),
                    timestamp=message_timestamp,
                    turn=turn_index,
                )
            )

        assistant_message = turn.get("assistant_message") or {}
        if assistant_message.get("text"):
            message_timestamp = assistant_message.get("created_at") or turn.get(
                "created_at"
            )
            events.append(
                stamp_realtime_feedback_event(
                    build_bot_text_event(
                        text=assistant_message["text"],
                        timestamp=message_timestamp,
                    ),
                    timestamp=message_timestamp,
                    turn=turn_index,
                )
            )

    return sorted(events, key=realtime_feedback_event_sort_key)
