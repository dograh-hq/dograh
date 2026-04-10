"""dedup recordings to org-scoped unique audio

Revision ID: 3cd3155084a2
Revises: e7254d2c6c18
Create Date: 2026-04-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3cd3155084a2"
down_revision: Union[str, None] = "e7254d2c6c18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Identify duplicate groups: same (org, transcript, tts config).
    #    Within each group the earliest row (by created_at) is canonical;
    #    every other row is an alias that will be remapped and soft-deleted.
    rows = conn.execute(
        sa.text("""
            WITH ranked AS (
                SELECT
                    recording_id,
                    organization_id,
                    transcript,
                    tts_provider,
                    tts_model,
                    tts_voice_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY organization_id, transcript,
                                     tts_provider, tts_model, tts_voice_id
                        ORDER BY created_at ASC
                    ) AS rn
                FROM workflow_recordings
                WHERE is_active = true
            ),
            canonical AS (
                SELECT recording_id AS canonical_id,
                       organization_id, transcript,
                       tts_provider, tts_model, tts_voice_id
                FROM ranked
                WHERE rn = 1
            )
            SELECT r.recording_id AS alias_id, c.canonical_id
            FROM ranked r
            JOIN canonical c
              ON  r.organization_id = c.organization_id
              AND r.transcript      = c.transcript
              AND r.tts_provider    = c.tts_provider
              AND r.tts_model       = c.tts_model
              AND r.tts_voice_id    = c.tts_voice_id
            WHERE r.rn > 1
        """)
    ).fetchall()

    if not rows:
        return

    # 2. Replace alias recording_ids with canonical ones in workflow JSON.
    #    Both draft definitions (workflows.workflow_definition) and published
    #    versions (workflow_definitions.workflow_json) are updated.
    for alias_id, canonical_id in rows:
        alias_pattern = f"RECORDING_ID: {alias_id}"
        canonical_pattern = f"RECORDING_ID: {canonical_id}"
        conn.execute(
            sa.text("""
                UPDATE workflows
                SET workflow_definition =
                    REPLACE(workflow_definition::text, :alias, :canonical)::json
                WHERE workflow_definition::text LIKE '%%' || :alias || '%%'
            """),
            {"alias": alias_pattern, "canonical": canonical_pattern},
        )
        conn.execute(
            sa.text("""
                UPDATE workflow_definitions
                SET workflow_json =
                    REPLACE(workflow_json::text, :alias, :canonical)::json
                WHERE workflow_json::text LIKE '%%' || :alias || '%%'
            """),
            {"alias": alias_pattern, "canonical": canonical_pattern},
        )

    # 3. Soft-delete every alias row.
    alias_ids = [r[0] for r in rows]
    conn.execute(
        sa.text("""
            UPDATE workflow_recordings
            SET is_active = false
            WHERE recording_id = ANY(:ids)
              AND is_active = true
        """),
        {"ids": alias_ids},
    )


def downgrade() -> None:
    # Deduplication is a one-way data migration.  The soft-deleted rows
    # still exist in the table; a manual restore is possible if needed.
    pass
