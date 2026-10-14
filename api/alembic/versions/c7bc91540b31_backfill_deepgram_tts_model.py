"""Backfill the Deepgram TTS model setting from stored voices.

Revision ID: c7bc91540b31
Revises: c82d4f6a9b10

Deepgram TTS settings used to carry only a voice; the voice family is now an
explicit ``model`` setting that the form shows and gates other settings on.
Stored settings without it would display the default family until re-saved.
This writes the family the voice id names into every Deepgram TTS block of a
named configuration, a workflow's inline override and a definition's inline
override. The inference is a frozen copy of the application rule at this
revision and operates on raw JSON, so the migration never imports application
code. Blocks that already carry a model are left alone, so the backfill can
run more than once.

Downgrade removes the key again: the reader before this revision rejects it as
an unknown setting.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "c7bc91540b31"
down_revision = "c82d4f6a9b10"
branch_labels = None
depends_on = None

BINDING_KEY = "model_configuration_override"

# Frozen copy of api.services.configuration.options.deepgram at this revision.
_VOICE_PREFIXES = (("flux-", "flux"), ("aura-2-", "aura-2"), ("aura-", "aura-1"))
_DEFAULT_MODEL = "aura-2"

# (table, JSON column, path from the column's root to the object holding "tts")
_TARGETS = (
    ("model_configurations", "configuration", ()),
    ("workflows", "workflow_configurations", (BINDING_KEY,)),
    ("workflow_definitions", "workflow_configurations", (BINDING_KEY,)),
)


def model_for_voice(voice):
    for prefix, model in _VOICE_PREFIXES:
        if isinstance(voice, str) and voice.startswith(prefix):
            return model
    return _DEFAULT_MODEL


def _as_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _settings(payload, path, deepgram_uuids):
    """The settings dict of a Deepgram TTS block under ``path``, or None."""
    root = payload
    for key in path:
        root = root.get(key) if isinstance(root, dict) else None
    tts = root.get("tts") if isinstance(root, dict) else None
    if not isinstance(tts, dict):
        return None
    if str(tts.get("provider_connection_uuid")) not in deepgram_uuids:
        return None
    settings = tts.get("settings")
    return settings if isinstance(settings, dict) else None


def add_model(payload, path, deepgram_uuids):
    """Write the voice's family as the model; True when the payload changed."""
    settings = _settings(payload, path, deepgram_uuids)
    if settings is None or "model" in settings:
        return False
    settings["model"] = model_for_voice(settings.get("voice"))
    return True


def remove_model(payload, path, deepgram_uuids):
    settings = _settings(payload, path, deepgram_uuids)
    if settings is None or "model" not in settings:
        return False
    del settings["model"]
    return True


def migrate(connection, change):
    """Apply ``change`` to every Deepgram TTS block; return rows updated per table."""
    deepgram_uuids = {
        str(row.uuid)
        for row in connection.execute(
            sa.text("SELECT uuid FROM provider_connections WHERE provider = 'deepgram'")
        )
    }
    summary = {}
    for table, column, path in _TARGETS:
        if not deepgram_uuids:
            summary[table] = 0
            continue
        # Only rows with a TTS settings block can change; the uuid check happens
        # in Python so the SQL stays a path test.
        settings_path = "{" + ",".join((*path, "tts", "settings")) + "}"
        rows = connection.execute(
            sa.text(
                f"SELECT id, {column} AS payload FROM {table} "
                f"WHERE ({column}::jsonb #> :path) IS NOT NULL"
            ),
            {"path": settings_path},
        )
        updates = []
        for row in rows:
            payload = _as_object(row.payload)
            if payload is not None and change(payload, path, deepgram_uuids):
                updates.append({"id": row.id, "payload": json.dumps(payload)})
        if updates:
            connection.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE id = :id"),
                updates,
            )
        summary[table] = len(updates)
    return summary


def upgrade() -> None:
    summary = migrate(op.get_bind(), add_model)
    print(
        "Deepgram TTS model backfill: "
        + ", ".join(f"{count} {table} row(s)" for table, count in summary.items())
    )


def downgrade() -> None:
    migrate(op.get_bind(), remove_model)
