"""Conversation building, transcript formatting, and per-node event splitting."""

from collections import OrderedDict
from datetime import datetime

from pipecat.utils.enums import RealtimeFeedbackType


def _safe_parse_timestamp(event: dict) -> datetime | None:
    """Best-effort parse of an ISO timestamp from an event.

    Returns None if no valid timestamp is available.
    """
    # Prefer payload timestamp when present
    payload = event.get("payload") or {}
    candidates = [
        payload.get("timestamp"),
        event.get("timestamp"),
    ]

    for ts in candidates:
        if not ts:
            continue
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            continue

    return None


def build_conversation_structure(logs: list[dict]) -> list[dict]:
    """Transform raw call logs into a conversation structure for LLM QA analysis."""
    if not logs:
        return []

    start_time = datetime.fromisoformat(logs[0]["timestamp"])

    conversation = []
    for event in logs:
        if event["type"] == RealtimeFeedbackType.BOT_TEXT.value:
            speaker = "assistant"
            utterance_text = event["payload"]["text"]
            event_time = _safe_parse_timestamp(event) or start_time
        elif event["type"] == RealtimeFeedbackType.USER_TRANSCRIPTION.value and event[
            "payload"
        ].get("final", False):
            speaker = "user"
            utterance_text = event["payload"]["text"]
            event_time = _safe_parse_timestamp(event) or start_time
        elif event["type"] == RealtimeFeedbackType.FUNCTION_CALL_START.value:
            speaker = "tool_call"
            payload = event["payload"]
            utterance_text = payload.get("function_name", "unknown")
            event_time = _safe_parse_timestamp(event) or start_time
        else:
            continue

        time_from_start = (event_time - start_time).total_seconds()

        conversation.append(
            {
                "time_from_start_seconds": round(time_from_start, 2),
                "speaker": speaker,
                "text": utterance_text,
                "node_name": event.get("node_name", ""),
                "turn": event.get("turn", 0),
            }
        )

    return conversation


def format_transcript(conversation: list[dict]) -> str:
    """Format conversation structure into a readable transcript string for the LLM."""
    lines = []
    for entry in conversation:
        if entry["speaker"] == "tool_call":
            lines.append(
                f"[{entry['time_from_start_seconds']:.1f}s] "
                f"[tool_call]: {entry['text']}"
            )
        else:
            lines.append(
                f"[{entry['time_from_start_seconds']:.1f}s] "
                f"{entry['speaker']}: {entry['text']}"
            )
    return "\n".join(lines)


def split_events_by_node(
    rtf_events: list[dict],
) -> list[tuple[str, str, list[dict]]]:
    """Split realtime_feedback_events by node_id.

    Returns an ordered list of (node_id, node_name, events) tuples.
    Only includes nodes that have conversational content (BOT_TEXT or USER_TRANSCRIPTION).
    """
    conversational_types = {
        RealtimeFeedbackType.BOT_TEXT.value,
        RealtimeFeedbackType.USER_TRANSCRIPTION.value,
    }

    # Preserve insertion order — first occurrence defines position
    node_events: OrderedDict[str, list[dict]] = OrderedDict()
    node_names: dict[str, str] = {}

    for event in rtf_events:
        node_id = event.get("node_id")
        if not node_id:
            return []  # Events lack node_id — caller should fall back

        if node_id not in node_events:
            node_events[node_id] = []
            node_names[node_id] = event.get("node_name", "")

        node_events[node_id].append(event)

    # Filter to nodes with conversational content
    result = []
    for node_id, events in node_events.items():
        has_conversation = any(e["type"] in conversational_types for e in events)
        if has_conversation:
            result.append((node_id, node_names[node_id], events))

    return result
