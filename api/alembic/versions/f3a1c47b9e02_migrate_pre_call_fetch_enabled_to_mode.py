"""migrate pre_call_fetch_enabled to pre_call_fetch_mode

Revision ID: f3a1c47b9e02
Revises: d4b83a1f6c27
Create Date: 2026-08-22 09:40:00.000000

"""

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a1c47b9e02"
down_revision: Union[str, None] = "d4b83a1f6c27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The Start node's `pre_call_fetch_enabled` boolean was replaced by the
# four-valued `pre_call_fetch_mode` (disabled/always/inbound/outbound). Until
# now the old flag was translated on read, in three separate places. That held
# for the runtime and the UI but not for the MCP path: `get_workflow_code`
# generates SDK TypeScript straight from the stored JSON and drops any field
# the node spec doesn't declare, so an agent that read a legacy workflow and
# saved it back lost the flag entirely and the node fell to `disabled` — with
# `pre_call_fetch_url` still populated, so it looked configured. Writing the
# mode into stored data removes the read-time translation and closes that hole.
#
# Only `enabled: true` needs rewriting. A node with `enabled: false` and no
# mode already resolves to `disabled`, so it is left alone.
#
# `pre_call_fetch_enabled` is deliberately NOT removed: leaving it keeps a
# rollback to the previous release readable, and it disappears on its own the
# next time the node is saved (node data models ignore unknown fields). A
# later cleanup migration can drop it once the release has settled.
#
# Run history (workflow_runs, workflow_run_text_sessions) records what actually
# executed and is not rewritten.

# (table, primary key column, json column)
_TARGETS = [
    ("workflow_definitions", "id", "workflow_json"),
    ("workflows", "id", "workflow_definition"),
    ("workflow_templates", "id", "template_json"),
]

_LEGACY_KEY = "pre_call_fetch_enabled"
_MODE_KEY = "pre_call_fetch_mode"


def _migrate_nodes(payload: Any, *, to_mode: bool) -> bool:
    """Rewrite Start nodes in place. Returns True if anything changed.

    `to_mode=True` fills the mode from the legacy flag; `to_mode=False` is the
    downgrade, which drops the mode again on nodes that still carry the flag.
    """
    if not isinstance(payload, dict):
        return False
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return False

    changed = False
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "startCall":
            continue
        data = node.get("data")
        if not isinstance(data, dict) or _LEGACY_KEY not in data:
            continue

        if to_mode:
            # An explicit mode wins — it was set after the flag went stale.
            if data.get(_MODE_KEY) is None and data.get(_LEGACY_KEY) is True:
                data[_MODE_KEY] = "always"
                changed = True
        elif data.get(_MODE_KEY) == "always" and data.get(_LEGACY_KEY) is True:
            # Undo only what upgrade() could have written. A node carrying the
            # flag alongside any other mode was set deliberately, after the
            # flag went stale, and must keep it.
            del data[_MODE_KEY]
            changed = True

    return changed


# Definitions average ~30 KB and run to ~800 KB, and thousands of rows carry
# the legacy key, so payloads are pulled a chunk at a time rather than all at
# once.
_CHUNK = 200


def _rewrite(to_mode: bool) -> None:
    conn = op.get_bind()

    for table, pk, column in _TARGETS:
        # Filter on the raw text, never on a jsonb predicate: a handful of rows
        # hold prompts with mojibake'd smart quotes (lone UTF-16 surrogates)
        # that Python's json accepts but `::jsonb` rejects, and a jsonb WHERE
        # clause is evaluated across every scanned row. None of those rows
        # carry the legacy key, so a text filter never reaches them.
        candidate_ids = [
            row[0]
            for row in conn.execute(
                sa.text(
                    f"SELECT {pk} FROM {table} WHERE {column}::text LIKE :needle "
                    f"ORDER BY {pk}"
                ),
                {"needle": f"%{_LEGACY_KEY}%"},
            )
        ]

        updated = 0
        skipped = 0
        for start in range(0, len(candidate_ids), _CHUNK):
            chunk = candidate_ids[start : start + _CHUNK]
            rows = conn.execute(
                sa.text(
                    f"SELECT {pk}, {column}::text AS payload FROM {table} "
                    f"WHERE {pk} IN :ids"
                ).bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": chunk},
            ).fetchall()

            for row_id, raw in rows:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    # Unparseable stored JSON is pre-existing damage; skip it
                    # loudly rather than failing the whole migration.
                    skipped += 1
                    print(f"  SKIPPED unparseable JSON: {table}.{pk}={row_id}")
                    continue

                if not _migrate_nodes(payload, to_mode=to_mode):
                    continue

                conn.execute(
                    sa.text(
                        f"UPDATE {table} SET {column} = CAST(:payload AS json) "
                        f"WHERE {pk} = :row_id"
                    ),
                    # ensure_ascii keeps lone surrogates as \uXXXX escapes, so
                    # rows Postgres would reject as jsonb still round-trip here.
                    {"payload": json.dumps(payload), "row_id": row_id},
                )
                updated += 1

        direction = "->mode" if to_mode else "->legacy"
        print(
            f"{table}.{column} {direction}: {updated} of {len(candidate_ids)} "
            f"candidate row(s) rewritten, {skipped} skipped"
        )


def upgrade() -> None:
    _rewrite(to_mode=True)


def downgrade() -> None:
    # The legacy flag was never removed, so reverting means dropping the mode
    # from the nodes that still carry the flag. Nodes authored after the switch
    # have no flag to fall back on and keep their mode.
    _rewrite(to_mode=False)
