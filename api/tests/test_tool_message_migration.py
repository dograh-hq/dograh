"""Legacy announcements migrate once, without changing control-tool behavior."""

import importlib
import json

import pytest
import sqlalchemy as sa

from api.schemas.tool import CreateToolRequest

migration = importlib.import_module(
    "api.alembic.versions.c82d4f6a9b10_unify_tool_messages"
)


@pytest.mark.parametrize(
    "category,config,expected",
    [
        (
            "http_api",
            {"customMessage": "Checking."},
            [{"event": "start", "type": "text", "text": "Checking."}],
        ),
        (
            "http_api",
            {
                "customMessageType": "audio",
                "customMessageRecordingId": "42",
                "customMessage": "Unused",
            },
            [{"event": "start", "type": "audio", "recording_id": 42}],
        ),
        (
            "end_call",
            {"messageType": "custom", "customMessage": "Goodbye."},
            [{"event": "start", "type": "text", "text": "Goodbye."}],
        ),
        (
            "transfer_call",
            {"messageType": "audio", "audioRecordingId": "42"},
            [{"event": "start", "type": "audio", "recording_id": 42}],
        ),
        (
            "transfer_agent",
            {"message": "Connecting {{name}}."},
            [{"event": "start", "type": "text", "text": "Connecting {{name}}."}],
        ),
        ("end_call", {"messageType": "none", "customMessage": "Unused"}, []),
        ("transfer_call", {"customMessage": "Unused"}, []),
        (
            "http_api",
            {
                "customMessageType": "audio",
                "customMessage": "Unused",
                "customMessageRecordingId": None,
            },
            [],
        ),
        ("http_api", {"customMessage": "  "}, []),
        ("transfer_agent", {"message": ""}, []),
        ("mcp", {"url": "https://example.com/mcp"}, []),
        ("calculator", {}, []),
    ],
)
def test_legacy_conversion_is_idempotent_and_matches_current_schema(
    category, config, expected
):
    base_config = {
        "http_api": {"method": "POST", "url": "https://example.com"},
        "transfer_agent": {"workflow_id": 1},
    }.get(category, {})
    original = {"type": category, "schema_version": 1}
    if category != "calculator":
        original["config"] = {**base_config, **config}
    converted = migration.migrate_definition(original, category)
    assert converted["messages"] == expected
    assert migration.migrate_definition(converted, category) == converted
    for field in migration.LEGACY_FIELDS.get(category, ()):
        assert field not in converted["config"]
    validated = CreateToolRequest(name="Tool", definition=converted)
    assert [m.model_dump() for m in validated.definition.messages] == expected


def test_existing_array_and_other_config_are_preserved():
    original = {
        "type": "transfer_call",
        "messages": [],
        "config": {
            "messageType": "custom",
            "customMessage": "Unused",
            "destination": "+1234567890",
            "resolver": {"wait_message": "Resolving."},
        },
    }
    converted = migration.migrate_definition(original, "transfer_call")
    assert converted["messages"] == []
    assert converted["config"] == {
        "destination": "+1234567890",
        "resolver": {"wait_message": "Resolving."},
    }
    assert "customMessage" in original["config"]  # no in-place mutation


def test_downgrade_preserves_single_messages_and_refuses_lossy_conversion():
    for category in migration.LEGACY_FIELDS:
        definition = {
            "type": category,
            "config": {},
            "messages": [{"event": "start", "type": "text", "text": "Hello."}],
        }
        assert (
            migration.migrate_definition(
                migration.restore_definition(definition, category), category
            )
            == definition
        )
        with pytest.raises(ValueError, match="Cannot downgrade"):
            migration.restore_definition(
                {**definition, "messages": definition["messages"] * 2}, category
            )
    with pytest.raises(ValueError, match="Cannot downgrade"):
        migration.restore_definition(
            {
                "messages": [
                    {
                        "event": "delayed",
                        "after_ms": 1000,
                        "type": "text",
                        "text": "Wait.",
                    }
                ]
            },
            "http_api",
        )


@pytest.mark.asyncio
async def test_migration_updates_json_for_active_and_archived_tools_across_orgs(
    test_engine,
):
    async with test_engine.begin() as connection:
        # A temporary table shadows the real table on this connection only.
        await connection.execute(
            sa.text(
                "CREATE TEMP TABLE tools (id int PRIMARY KEY, category text, definition json, organization_id int, status text) ON COMMIT DROP"
            )
        )
        for tool_id, category, config, organization, status in [
            (
                1,
                "http_api",
                {
                    "customMessage": "Checking.",
                    "method": "POST",
                    "url": "https://example.com",
                },
                10,
                "active",
            ),
            (
                2,
                "end_call",
                {"messageType": "audio", "audioRecordingId": "42"},
                20,
                "archived",
            ),
            (3, "native", {"customMessage": "Unrelated."}, 10, "active"),
        ]:
            await connection.execute(
                sa.text(
                    "INSERT INTO tools VALUES (:id, :category, CAST(:definition AS json), :org, :status)"
                ),
                {
                    "id": tool_id,
                    "category": category,
                    "definition": json.dumps({"type": category, "config": config}),
                    "org": organization,
                    "status": status,
                },
            )
        await connection.run_sync(migration.migrate)
        first = (
            (await connection.execute(sa.text("SELECT * FROM tools ORDER BY id")))
            .mappings()
            .all()
        )
        assert first[0]["definition"]["messages"][0]["text"] == "Checking."
        assert first[1]["definition"]["messages"][0]["recording_id"] == 42
        assert first[1]["status"] == "archived"
        assert "messages" not in first[2]["definition"]
        await connection.run_sync(migration.migrate)
        assert (
            await connection.execute(sa.text("SELECT * FROM tools ORDER BY id"))
        ).mappings().all() == first
        await connection.run_sync(lambda conn: migration.migrate(conn, downgrade=True))
        restored = (
            await connection.execute(
                sa.text("SELECT definition FROM tools WHERE id = 2")
            )
        ).scalar_one()
        assert restored["config"]["audioRecordingId"] == "42"
