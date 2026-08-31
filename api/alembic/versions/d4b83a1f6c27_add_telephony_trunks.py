"""promote outbound SIP trunks to rows and attach phone numbers to them

Trunks used to live as a JSON list inside ``telephony_configurations.credentials``
while phone numbers hung off the configuration, so the caller ID and the trunk a
call went out on were chosen from separate pools. As soon as a configuration
holds two trunks that pairing is wrong half the time, and a carrier will reject
— or decline to attest — a caller ID it does not own.

The backfill is deliberately conservative:

* Trunks saved without a name are dropped. They are placeholders left behind
  when someone opened the SIP card and picked a region without filling the form
  in; nothing reads their region back, and the trunk name is what Cloudonix
  keys on.
* Phone numbers are attached only where the configuration ends up with exactly
  one trunk. With two or more there is no way to tell from here which carrier
  authorised which number, so those are left for the operator to assign.

Revision ID: d4b83a1f6c27
Revises: c7a1e4f93b26
Create Date: 2026-08-17 10:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b83a1f6c27"
down_revision: Union[str, None] = "c7a1e4f93b26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telephony_trunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telephony_configuration_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["telephony_configuration_id"],
            ["telephony_configurations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telephony_configuration_id",
            "name",
            name="uq_telephony_trunks_config_name",
        ),
    )
    op.create_index(op.f("ix_telephony_trunks_id"), "telephony_trunks", ["id"])
    op.create_index(
        "ix_telephony_trunks_config", "telephony_trunks", ["telephony_configuration_id"]
    )

    op.add_column(
        "telephony_phone_numbers",
        sa.Column("telephony_trunk_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_phone_numbers_trunk",
        "telephony_phone_numbers",
        "telephony_trunks",
        ["telephony_trunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_phone_numbers_trunk",
        "telephony_phone_numbers",
        ["telephony_trunk_id"],
        postgresql_where=sa.text("telephony_trunk_id IS NOT NULL"),
    )

    # One row per named trunk, keeping the stored order so the first enabled
    # trunk — the only one calls ever used — keeps the lowest id.
    op.execute(
        """
        INSERT INTO telephony_trunks (
            telephony_configuration_id, name, enabled, settings, external_id,
            created_at, updated_at
        )
        SELECT c.id,
               trim(entry.trunk->>'name'),
               coalesce((entry.trunk->>'enabled')::boolean, false),
               jsonb_strip_nulls(
                   jsonb_build_object(
                       'region', entry.trunk->>'region',
                       'sip_domain', entry.trunk->>'sip_domain'
                   )
               )::json,
               c.credentials::jsonb->'outbound_trunk_uuids'->>(entry.trunk->>'id'),
               now(),
               now()
        FROM telephony_configurations c
        CROSS JOIN LATERAL jsonb_array_elements(
            c.credentials::jsonb->'outbound_trunks'
        ) WITH ORDINALITY AS entry(trunk, position)
        WHERE jsonb_typeof(c.credentials::jsonb->'outbound_trunks') = 'array'
          AND nullif(trim(coalesce(entry.trunk->>'name', '')), '') IS NOT NULL
        ORDER BY c.id, entry.position
        """
    )

    # Only unambiguous when the configuration has a single trunk.
    op.execute(
        """
        UPDATE telephony_phone_numbers p
        SET telephony_trunk_id = sole.trunk_id
        FROM (
            SELECT telephony_configuration_id, min(id) AS trunk_id
            FROM telephony_trunks
            GROUP BY telephony_configuration_id
            HAVING count(*) = 1
        ) sole
        WHERE sole.telephony_configuration_id = p.telephony_configuration_id
        """
    )

    # The rows are authoritative now; leaving the JSON behind would give the
    # next reader two answers to the same question.
    op.execute(
        """
        UPDATE telephony_configurations
        SET credentials = (
            (credentials::jsonb) - 'outbound_trunks' - 'outbound_trunk_uuids'
        )::json
        WHERE (credentials::jsonb) - 'outbound_trunks' - 'outbound_trunk_uuids'
              <> credentials::jsonb
        """
    )


def downgrade() -> None:
    # Fold the rows back into the credentials blob so a rollback keeps working
    # trunks. The Dograh-side trunk id was the row's primary key from here on,
    # so it is written back as text.
    op.execute(
        """
        UPDATE telephony_configurations c
        SET credentials = (
            (c.credentials::jsonb)
            || jsonb_build_object('outbound_trunks', folded.trunks)
            || jsonb_build_object('outbound_trunk_uuids', folded.uuids)
        )::json
        FROM (
            SELECT t.telephony_configuration_id AS config_id,
                   jsonb_agg(
                       jsonb_strip_nulls(
                           jsonb_build_object(
                               'id', t.id::text,
                               'enabled', t.enabled,
                               'name', t.name,
                               'region', t.settings::jsonb->>'region',
                               'sip_domain', t.settings::jsonb->>'sip_domain'
                           )
                       )
                       ORDER BY t.id
                   ) AS trunks,
                   coalesce(
                       jsonb_object_agg(t.id::text, t.external_id)
                           FILTER (WHERE t.external_id IS NOT NULL),
                       '{}'::jsonb
                   ) AS uuids
            FROM telephony_trunks t
            GROUP BY t.telephony_configuration_id
        ) folded
        WHERE folded.config_id = c.id
        """
    )

    op.drop_index("ix_phone_numbers_trunk", table_name="telephony_phone_numbers")
    op.drop_constraint(
        "fk_phone_numbers_trunk", "telephony_phone_numbers", type_="foreignkey"
    )
    op.drop_column("telephony_phone_numbers", "telephony_trunk_id")
    op.drop_index("ix_telephony_trunks_config", table_name="telephony_trunks")
    op.drop_index(op.f("ix_telephony_trunks_id"), table_name="telephony_trunks")
    op.drop_table("telephony_trunks")
