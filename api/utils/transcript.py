from typing import List

from pipecat.utils.enums import RealtimeFeedbackType


def generate_transcript_text(events: List[dict]) -> str:
    """Generate transcript text from realtime feedback events.

    Filters for rtf-user-transcription (final) and rtf-bot-text events,
    formats them as '[timestamp] user/assistant: text\\n'.
    """
    lines: List[str] = []
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload", {})

        if (
            event_type == RealtimeFeedbackType.USER_TRANSCRIPTION.value
            and payload.get("final") is True
        ):
            timestamp = payload.get("timestamp") or event.get("timestamp", "")
            prefix = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{prefix}user: {payload.get('text', '')}\n")
        elif event_type == RealtimeFeedbackType.BOT_TEXT.value:
            timestamp = payload.get("timestamp") or event.get("timestamp", "")
            prefix = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{prefix}assistant: {payload.get('text', '')}\n")

    return "".join(lines)
