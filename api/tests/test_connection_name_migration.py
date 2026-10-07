"""The name cleanup changes only exact generated labels, within each org."""

import importlib

import sqlalchemy as sa

migration = importlib.import_module(
    "api.alembic.versions.c81f27e04a93_clean_automatic_connection_names"
)


def test_rename_preserves_custom_names_and_scopes_collisions_to_organization():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE provider_connections (id INTEGER PRIMARY KEY, "
                "organization_id INTEGER, provider TEXT, name TEXT, "
                "revision INTEGER DEFAULT 1, updated_at TIMESTAMP, credentials TEXT)"
            )
        )
        source = [
            (1, 10, "dograh", "Dograh"),
            (2, 10, "dograh", "Imported dograh connection 1"),
            (3, 10, "dograh", "Imported dograh connection 2"),
            (4, 10, "openai", "Imported openai connection 3"),
            (5, 20, "dograh", "Imported dograh connection 1"),
            (6, 10, "dograh", "Imported dograh connection 4 - production"),
            (7, 10, "openai", "Support production"),
            (8, 10, "openai", "Imported dograh connection 5"),
        ]
        connection.execute(
            sa.text(
                "INSERT INTO provider_connections (id, organization_id, provider, name, credentials) "
                "VALUES (:id, :organization_id, :provider, :name, 'unchanged')"
            ),
            [
                dict(zip(("id", "organization_id", "provider", "name"), row))
                for row in source
            ],
        )

        assert migration._rename_connections(connection) == 4
        rows = (
            connection.execute(
                sa.text(
                    "SELECT id, name, revision, credentials FROM provider_connections ORDER BY id"
                )
            )
            .mappings()
            .all()
        )
        assert [row["name"] for row in rows] == [
            "Dograh",
            "Dograh 2",
            "Dograh 3",
            "OpenAI",
            "Dograh",
            source[5][3],
            "Support production",
            source[7][3],
        ]
        assert [row["revision"] for row in rows] == [1, 2, 2, 2, 2, 1, 1, 1]
        assert all(row["credentials"] == "unchanged" for row in rows)
        assert migration._rename_connections(connection) == 0
    engine.dispose()
