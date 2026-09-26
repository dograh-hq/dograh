"""harden CALMOS caller identity, privacy, replay, and artifact persistence

Revision ID: c4a81f0e2b7d
Revises: b9e0c3a5d7f1
Create Date: 2026-09-06 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a81f0e2b7d"
down_revision: str | None = "b9e0c3a5d7f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The preceding CALMOS migration enables vector. Keep this idempotent so a
    # database restored from a partial snapshot still has the required type.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "service_users",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "service_users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "service_users",
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "service_users",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.execute(
        "UPDATE service_users SET "
        "created_at = COALESCE(first_seen_at, now()), "
        "updated_at = COALESCE(last_seen_at, first_seen_at, now())"
    )
    op.alter_column("service_users", "created_at", nullable=False)
    op.alter_column("service_users", "updated_at", nullable=False)
    # New identities live in caller_identifiers. The old hash remains nullable
    # so pre-1.46.0.3 rows can be matched and upgraded without plaintext data.
    op.alter_column(
        "service_users",
        "caller_identifier_hash",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.create_index(
        "ix_service_users_org_status",
        "service_users",
        ["organization_id", "status"],
        unique=False,
    )

    op.create_table(
        "caller_identifiers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("service_user_id", sa.String(length=36), nullable=False),
        sa.Column("identifier_type", sa.String(length=32), nullable=False),
        sa.Column("identifier_value_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "hash_scheme",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'hmac_sha256_v1'"),
        ),
        sa.Column(
            "verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "verification_level",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["service_user_id"], ["service_users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "identifier_type",
            "identifier_value_hash",
            name="uq_caller_identifiers_org_type_hash",
        ),
    )
    op.create_index(
        "ix_caller_identifiers_lookup",
        "caller_identifiers",
        ["organization_id", "identifier_type", "identifier_value_hash"],
        unique=False,
    )
    op.create_index(
        "ix_caller_identifiers_service_user_id",
        "caller_identifiers",
        ["service_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_caller_identifiers_last_seen_at",
        "caller_identifiers",
        ["last_seen_at"],
        unique=False,
    )
    # Plaintext identifiers were never retained on service_users, so preserve
    # historical hashes as legacy lookup rows. A future observed identifier is
    # upgraded to HMAC by application code after a successful legacy match.
    op.execute(
        """
        INSERT INTO caller_identifiers (
            id, organization_id, service_user_id, identifier_type,
            identifier_value_hash, hash_scheme, verified, verification_level,
            created_at, last_seen_at
        )
        SELECT
            gen_random_uuid()::text,
            organization_id,
            id,
            'phone',
            caller_identifier_hash,
            'sha256_v1',
            false,
            'none',
            COALESCE(first_seen_at, now()),
            COALESCE(last_seen_at, first_seen_at, now())
        FROM service_users
        WHERE caller_identifier_hash IS NOT NULL
        ON CONFLICT (organization_id, identifier_type, identifier_value_hash)
        DO NOTHING
        """
    )

    op.add_column(
        "workflow_runs",
        sa.Column("caller_identifier_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "caller_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("provider_call_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("transcript_object_key", sa.String(), nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "latency_metrics",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        "UPDATE workflow_runs SET caller_state = 'RECOGNISED' "
        "WHERE service_user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET provider_call_id = gathered_context->>'call_id' "
        "WHERE provider_call_id IS NULL AND gathered_context->>'call_id' IS NOT NULL"
    )
    op.execute(
        "UPDATE workflow_runs SET transcript_object_key = transcript_url "
        "WHERE transcript_url IS NOT NULL "
        "AND transcript_url !~* '^https?://'"
    )
    op.create_foreign_key(
        "fk_workflow_runs_caller_identifier_id",
        "workflow_runs",
        "caller_identifiers",
        ["caller_identifier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workflow_runs_caller_identifier_id",
        "workflow_runs",
        ["caller_identifier_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_caller_state",
        "workflow_runs",
        ["caller_state"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_created_at",
        "workflow_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_provider_call_id",
        "workflow_runs",
        ["provider_call_id"],
        unique=False,
        postgresql_where=sa.text("provider_call_id IS NOT NULL"),
    )

    op.add_column(
        "recordings",
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_recordings_object_key", "recordings", ["object_key"], unique=False
    )
    op.create_index(
        "ix_recordings_created_at", "recordings", ["created_at"], unique=False
    )

    op.create_table(
        "privacy_permissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("service_user_id", sa.String(length=36), nullable=False),
        sa.Column("permission_type", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "verification_level",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("source_workflow_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "permission_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["service_user_id"], ["service_users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_permissions_service_user_id",
        "privacy_permissions",
        ["service_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_privacy_permissions_source_workflow_run_id",
        "privacy_permissions",
        ["source_workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_privacy_permissions_lookup",
        "privacy_permissions",
        ["service_user_id", "permission_type", "created_at"],
        unique=False,
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("service_user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "event_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["service_user_id"], ["service_users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_org_created_at",
        "audit_events",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_workflow_run_id",
        "audit_events",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_service_user_id",
        "audit_events",
        ["service_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_type_created_at",
        "audit_events",
        ["event_type", "created_at"],
        unique=False,
    )

    op.create_index(
        "ix_memories_service_user_active_expiry",
        "memories",
        ["service_user_id", "active", "expires_at"],
        unique=False,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_memories_service_user_active_expiry", table_name="memories")

    op.drop_index("ix_audit_events_type_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_service_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_workflow_run_id", table_name="audit_events")
    op.drop_index("ix_audit_events_org_created_at", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_privacy_permissions_lookup", table_name="privacy_permissions")
    op.drop_index(
        "ix_privacy_permissions_source_workflow_run_id",
        table_name="privacy_permissions",
    )
    op.drop_index(
        "ix_privacy_permissions_service_user_id", table_name="privacy_permissions"
    )
    op.drop_table("privacy_permissions")

    op.drop_index("ix_recordings_created_at", table_name="recordings")
    op.drop_index("ix_recordings_object_key", table_name="recordings")
    op.drop_column("recordings", "checksum_sha256")

    op.drop_index("ix_workflow_runs_provider_call_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_created_at", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_caller_state", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_caller_identifier_id", table_name="workflow_runs")
    op.drop_constraint(
        "fk_workflow_runs_caller_identifier_id", "workflow_runs", type_="foreignkey"
    )
    op.drop_column("workflow_runs", "latency_metrics")
    op.drop_column("workflow_runs", "transcript_object_key")
    op.drop_column("workflow_runs", "provider_call_id")
    op.drop_column("workflow_runs", "caller_state")
    op.drop_column("workflow_runs", "caller_identifier_id")

    op.drop_index("ix_caller_identifiers_last_seen_at", table_name="caller_identifiers")
    op.drop_index(
        "ix_caller_identifiers_service_user_id", table_name="caller_identifiers"
    )
    op.drop_index("ix_caller_identifiers_lookup", table_name="caller_identifiers")
    op.drop_table("caller_identifiers")

    # Older application images require this column. New 1.46.0.3 users do not
    # have a legacy SHA hash, so give them a deterministic non-PII placeholder
    # before restoring the NOT NULL constraint.
    op.execute(
        "UPDATE service_users SET caller_identifier_hash = "
        "encode(digest('service-user:' || id, 'sha256'), 'hex') "
        "WHERE caller_identifier_hash IS NULL"
    )
    op.drop_index("ix_service_users_org_status", table_name="service_users")
    op.alter_column(
        "service_users",
        "caller_identifier_hash",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.drop_column("service_users", "status")
    op.drop_column("service_users", "memory_enabled")
    op.drop_column("service_users", "updated_at")
    op.drop_column("service_users", "created_at")
