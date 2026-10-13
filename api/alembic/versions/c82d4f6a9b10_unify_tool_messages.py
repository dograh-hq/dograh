"""Move configured tool announcements into the shared messages list.

Revision ID: c82d4f6a9b10
Revises: b3c1d9e4f7a2
"""

import copy
import json

import sqlalchemy as sa
from alembic import op

revision = "c82d4f6a9b10"
down_revision = "b3c1d9e4f7a2"
branch_labels = None
depends_on = None

TOOL_TYPES = {
    "http_api",
    "mcp",
    "end_call",
    "transfer_call",
    "transfer_agent",
    "calculator",
}
LEGACY_FIELDS = {
    "http_api": ("customMessage", "customMessageType", "customMessageRecordingId"),
    "end_call": ("customMessage", "messageType", "audioRecordingId"),
    "transfer_call": ("customMessage", "messageType", "audioRecordingId"),
    "transfer_agent": ("message",),
}


def migrate_definition(definition: dict, category: str) -> dict:
    """Self-contained conversion, independent of application schemas."""
    result = copy.deepcopy(definition)
    config = result.get("config") or {}
    if result.get("messages") is None:
        messages = []
        text, recording = None, None
        if category == "http_api":
            if config.get("customMessageType") == "audio":
                recording = config.get("customMessageRecordingId")
            else:
                text = config.get("customMessage")
        elif category in {"end_call", "transfer_call"}:
            if config.get("messageType") == "audio":
                recording = config.get("audioRecordingId")
            elif config.get("messageType") == "custom":
                text = config.get("customMessage")
        elif category == "transfer_agent":
            text = config.get("message")
        if recording:
            recording_id = int(recording)
            if recording_id <= 0:
                raise ValueError("Recording ID must be positive")
            messages.append(
                {"event": "start", "type": "audio", "recording_id": recording_id}
            )
        elif text and text.strip():
            messages.append({"event": "start", "type": "text", "text": text})
        result["messages"] = messages
    elif not isinstance(result["messages"], list):
        raise ValueError("Expected messages to be a list")
    for field in LEGACY_FIELDS.get(category, ()):
        config.pop(field, None)
    return result


def restore_definition(definition: dict, category: str) -> dict:
    """Refuse a downgrade that would discard variants or delayed updates."""
    result = copy.deepcopy(definition)
    messages = result.pop("messages", [])
    if (
        len(messages) > 1
        or any(message.get("event") != "start" for message in messages)
        or (messages and category in {"mcp", "calculator"})
    ):
        raise ValueError(
            "Cannot downgrade tool messages with variants or delayed updates"
        )
    message = messages[0] if messages else None
    config = result.setdefault("config", {}) if category in LEGACY_FIELDS else {}
    if category == "http_api":
        if message:
            config["customMessageType"] = message["type"]
            if message["type"] == "text":
                config["customMessage"] = message["text"]
            else:
                config["customMessageRecordingId"] = str(message["recording_id"])
    elif category in {"end_call", "transfer_call"}:
        config["messageType"] = "none"
        if message:
            config["messageType"] = "custom" if message["type"] == "text" else "audio"
            field = "customMessage" if message["type"] == "text" else "audioRecordingId"
            config[field] = (
                message["text"]
                if message["type"] == "text"
                else str(message["recording_id"])
            )
    elif category == "transfer_agent":
        if message and message["type"] != "text":
            raise ValueError("Cannot downgrade an audio agent-transfer message")
        config["message"] = message["text"] if message else ""
    return result


def migrate(connection, *, downgrade=False):
    # Includes archived tools. Workflows reference these by UUID; run history
    # contains execution facts and is not rewritten. All organizations migrate.
    rows = connection.execute(
        sa.text("SELECT id, category, definition FROM tools ORDER BY id FOR UPDATE")
    ).mappings()
    for row in rows:
        if row["category"] not in TOOL_TYPES:
            continue
        definition = row["definition"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        convert = restore_definition if downgrade else migrate_definition
        try:
            converted = convert(definition, row["category"])
        except (ValueError, TypeError, AttributeError) as exc:
            # Fail atomically with a row identifier, without logging customer text.
            raise ValueError(
                f"Cannot migrate messages for tool {row['id']}: {exc}"
            ) from exc
        if converted != definition:
            connection.execute(
                sa.text(
                    "UPDATE tools SET definition = CAST(:definition AS json) WHERE id = :id"
                ),
                {"id": row["id"], "definition": json.dumps(converted)},
            )


def upgrade():
    migrate(op.get_bind())


def downgrade():
    migrate(op.get_bind(), downgrade=True)
