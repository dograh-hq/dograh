"""add durable CALMOS call, recording and memory storage

Revision ID: 4f1c2d3e5a67
Revises: 8d7f2c1a4b6e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = "4f1c2d3e5a67"
down_revision: Union[str, None] = "8d7f2c1a4b6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions are idempotent. pgcrypto is already used by earlier Dograh
    # migrations, but keeping this explicit makes fresh RDS installs safe.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "service_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("caller_identifier_hash", sa.String(length=128), nullable=False),
        sa.Column("preferred_name", sa.String(length=200), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_use_explanation_shown",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "caller_identifier_hash",
            name="uq_service_users_org_caller_hash",
        ),
    )
    op.create_index(
        "ix_service_users_org_caller_hash",
        "service_users",
        ["organization_id", "caller_identifier_hash"],
        unique=False,
    )

    workflow_run_columns = (
        ("call_id", sa.String(length=64)),
        ("scenario_id", sa.String(length=128)),
        ("scenario_name", sa.String()),
        ("service_user_id", sa.String(length=36)),
        ("caller_identifier", sa.String(length=255)),
        ("telephone_number", sa.String(length=255)),
        ("direction", sa.String(length=32)),
        ("started_at", sa.DateTime(timezone=True)),
        ("connected_at", sa.DateTime(timezone=True)),
        ("ended_at", sa.DateTime(timezone=True)),
        ("duration_seconds", sa.Float()),
        ("call_status", sa.String(length=64)),
        ("telephony_provider", sa.String(length=64)),
        ("model_provider", sa.String(length=128)),
        ("stt_provider", sa.String(length=128)),
        ("tts_provider", sa.String(length=128)),
        ("avatar_provider", sa.String(length=128)),
        ("recording_object_key", sa.String()),
        ("recording_duration_seconds", sa.Float()),
        ("recording_format", sa.String(length=32)),
        ("recording_size_bytes", sa.Integer()),
        ("full_transcript", sa.Text()),
        ("termination_reason", sa.String(length=255)),
        (
            "debug_metadata",
            sa.JSON(),
        ),
    )
    for name, column_type in workflow_run_columns:
        op.add_column("workflow_runs", sa.Column(name, column_type, nullable=True))

    # Preserve all existing rows. A new canonical internal ID is generated for
    # historical rows; provider IDs remain in gathered_context for webhooks.
    op.execute(
        "UPDATE workflow_runs SET call_id = gen_random_uuid()::text "
        "WHERE call_id IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET started_at = created_at "
        "WHERE started_at IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET direction = COALESCE(initial_context->>'direction', call_type::text) "
        "WHERE direction IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET caller_identifier = COALESCE(initial_context->>'caller_number', initial_context->>'from_number') "
        "WHERE caller_identifier IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET telephone_number = COALESCE(initial_context->>'phone_number', initial_context->>'called_number') "
        "WHERE telephone_number IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET scenario_id = initial_context->>'scenario_id', "
        "scenario_name = COALESCE(initial_context->>'scenario_name', initial_context->>'scenario') "
        "WHERE scenario_id IS NULL OR scenario_name IS NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET call_status = CASE WHEN is_completed THEN 'completed' ELSE 'initialized' END "
        "WHERE call_status IS NULL"
    )
    op.execute("UPDATE workflow_runs SET debug_metadata = '{}'::json WHERE debug_metadata IS NULL")
    # Keep a database-side default so an older application image can be
    # rolled back without inserting NULL into the new non-null identity field.
    op.alter_column(
        "workflow_runs",
        "call_id",
        nullable=False,
        server_default=sa.text("gen_random_uuid()::text"),
    )
    op.alter_column("workflow_runs", "debug_metadata", nullable=False)
    op.create_index("ix_workflow_runs_call_id", "workflow_runs", ["call_id"], unique=True)
    op.create_index("ix_workflow_runs_scenario_id", "workflow_runs", ["scenario_id"], unique=False)
    op.create_index(
        "ix_workflow_runs_service_user_id",
        "workflow_runs",
        ["service_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_started_at",
        "workflow_runs",
        ["started_at"],
        unique=False,
    )
    op.create_index("ix_workflow_runs_ended_at", "workflow_runs", ["ended_at"], unique=False)
    op.create_index("ix_workflow_runs_call_status", "workflow_runs", ["call_status"], unique=False)
    op.create_foreign_key(
        "fk_workflow_runs_service_user_id",
        "workflow_runs",
        "service_users",
        ["service_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "utterances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calm_score", sa.Float(), nullable=True),
        sa.Column("safety_score", sa.Float(), nullable=True),
        sa.Column("clinical_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "sequence_number", name="uq_utterances_run_sequence"),
    )
    op.create_index("ix_utterances_agent_run_id", "utterances", ["agent_run_id"], unique=False)

    op.create_table(
        "recordings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("track", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "track", name="uq_recordings_run_track"),
    )
    op.create_index("ix_recordings_agent_run_id", "recordings", ["agent_run_id"], unique=False)

    op.create_table(
        "call_scores",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("calm_score", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("safety_score", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("clinical_evaluation", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index("ix_call_scores_agent_run_id", "call_scores", ["agent_run_id"], unique=False)

    op.create_table(
        "call_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.ForeignKeyConstraint(["agent_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_call_events_agent_run_id", "call_events", ["agent_run_id"], unique=False)
    op.create_index(
        "ix_call_events_type_occurred_at",
        "call_events",
        ["event_type", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("service_user_id", sa.String(length=36), nullable=False),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("memory_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("sensitivity", sa.String(length=32), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("source_agent_run_id", sa.Integer(), nullable=True),
        sa.Column("source_utterance_id", sa.String(length=36), nullable=True),
        sa.Column("internal_context_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("verbal_reference_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("explicit_detail_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["service_user_id"], ["service_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_agent_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_utterance_id"], ["utterances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memories_service_user_id", "memories", ["service_user_id"], unique=False)
    op.create_index("ix_memories_source_agent_run_id", "memories", ["source_agent_run_id"], unique=False)
    op.create_index("ix_memories_source_utterance_id", "memories", ["source_utterance_id"], unique=False)
    op.create_index("ix_memories_type_active", "memories", ["memory_type", "active"], unique=False)
    op.create_index(
        "ix_memories_active", "memories", ["active"], unique=False,
        postgresql_where=sa.text("active = true"),
    )
    op.create_index(
        "ix_memories_embedding_ivfflat",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "memory_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("utterance_id", sa.String(length=36), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["utterance_id"], ["utterances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_sources_memory_id", "memory_sources", ["memory_id"], unique=False)
    op.create_index("ix_memory_sources_agent_run_id", "memory_sources", ["agent_run_id"], unique=False)

    # Scenario metadata is optional for existing rows but searchable for all
    # current and future scenario-library records.
    op.add_column(
        "sakinah_scenarios",
        sa.Column("category", sa.String(length=128), nullable=True, server_default=sa.text("''")),
    )
    op.add_column(
        "sakinah_scenarios",
        sa.Column("tags", sa.JSON(), nullable=True, server_default=sa.text("'[]'::json")),
    )
    op.execute("UPDATE sakinah_scenarios SET category = '' WHERE category IS NULL")
    op.execute("UPDATE sakinah_scenarios SET tags = '[]'::json WHERE tags IS NULL")
    op.alter_column("sakinah_scenarios", "category", nullable=False)
    op.alter_column("sakinah_scenarios", "tags", nullable=False)
    op.create_index("ix_sakinah_scenarios_category", "sakinah_scenarios", ["category"], unique=False)
    op.execute(
        """
        CREATE INDEX ix_sakinah_scenarios_search_trgm
        ON sakinah_scenarios USING gin (
            (
                coalesce(id::text, '') || ' ' ||
                coalesce(title, '') || ' ' ||
                coalesce(category, '') || ' ' ||
                coalesce(tags::text, '') || ' ' ||
                coalesce(persona, '') || ' ' ||
                coalesce(language, '') || ' ' ||
                coalesce(communication_style, '') || ' ' ||
                coalesce(initial_information, '') || ' ' ||
                coalesce(hidden_information, '') || ' ' ||
                coalesce(disclosure, '') || ' ' ||
                coalesce(behaviour, '') || ' ' ||
                coalesce(background, '') || ' ' ||
                coalesce(additional_factors, '') || ' ' ||
                coalesce(notes, '') || ' ' ||
                coalesce(freestyle_prompt, '')
            ) gin_trgm_ops
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sakinah_scenarios_search_trgm")
    op.drop_index("ix_sakinah_scenarios_category", table_name="sakinah_scenarios")
    op.drop_column("sakinah_scenarios", "tags")
    op.drop_column("sakinah_scenarios", "category")

    op.drop_index("ix_memory_sources_agent_run_id", table_name="memory_sources")
    op.drop_index("ix_memory_sources_memory_id", table_name="memory_sources")
    op.drop_table("memory_sources")
    op.drop_index("ix_memories_embedding_ivfflat", table_name="memories")
    op.drop_index("ix_memories_active", table_name="memories")
    op.drop_index("ix_memories_type_active", table_name="memories")
    op.drop_index("ix_memories_source_utterance_id", table_name="memories")
    op.drop_index("ix_memories_source_agent_run_id", table_name="memories")
    op.drop_index("ix_memories_service_user_id", table_name="memories")
    op.drop_table("memories")

    op.drop_index("ix_call_events_type_occurred_at", table_name="call_events")
    op.drop_index("ix_call_events_agent_run_id", table_name="call_events")
    op.drop_table("call_events")
    op.drop_index("ix_call_scores_agent_run_id", table_name="call_scores")
    op.drop_table("call_scores")
    op.drop_index("ix_recordings_agent_run_id", table_name="recordings")
    op.drop_table("recordings")
    op.drop_index("ix_utterances_agent_run_id", table_name="utterances")
    op.drop_table("utterances")

    op.drop_constraint("fk_workflow_runs_service_user_id", "workflow_runs", type_="foreignkey")
    for name in (
        "ix_workflow_runs_call_status",
        "ix_workflow_runs_ended_at",
        "ix_workflow_runs_started_at",
        "ix_workflow_runs_service_user_id",
        "ix_workflow_runs_scenario_id",
        "ix_workflow_runs_call_id",
    ):
        op.drop_index(name, table_name="workflow_runs")
    for name, _column_type in (
        ("debug_metadata", None),
        ("termination_reason", None),
        ("full_transcript", None),
        ("recording_size_bytes", None),
        ("recording_format", None),
        ("recording_duration_seconds", None),
        ("recording_object_key", None),
        ("avatar_provider", None),
        ("tts_provider", None),
        ("stt_provider", None),
        ("model_provider", None),
        ("telephony_provider", None),
        ("call_status", None),
        ("duration_seconds", None),
        ("ended_at", None),
        ("connected_at", None),
        ("started_at", None),
        ("direction", None),
        ("telephone_number", None),
        ("caller_identifier", None),
        ("service_user_id", None),
        ("scenario_name", None),
        ("scenario_id", None),
        ("call_id", None),
    ):
        op.drop_column("workflow_runs", name)
    op.drop_index("ix_service_users_org_caller_hash", table_name="service_users")
    op.drop_table("service_users")
