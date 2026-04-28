"""Move ARI inbound_workflow_id from credentials JSONB to phone_numbers column.

In the legacy single-config-per-org model, the inbound workflow for an
Asterisk was stored as ``credentials.inbound_workflow_id`` on the telephony
config — one workflow for the whole connection, regardless of which extension
was dialed. The multi-config refactor moves inbound routing onto
``telephony_phone_numbers`` (one workflow per extension), matching every
other provider.

This migration copies the legacy value onto each linked phone number row
(only where the column is currently NULL — never overwrites a per-extension
assignment), then strips the now-unused key from credentials.

Revision ID: b3d7e0c4f8a2
Revises: a2355fc6bdc1
Create Date: 2026-04-28 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d7e0c4f8a2"
down_revision: Union[str, None] = "a2355fc6bdc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Backfill telephony_phone_numbers.inbound_workflow_id from each ARI
    #    config's credentials.inbound_workflow_id. Only fill NULLs.
    op.execute(
        """
        UPDATE telephony_phone_numbers tpn
        SET inbound_workflow_id = ((tc.credentials::jsonb)->>'inbound_workflow_id')::integer,
            updated_at = NOW()
        FROM telephony_configurations tc
        WHERE tpn.telephony_configuration_id = tc.id
          AND tc.provider = 'ari'
          AND (tc.credentials::jsonb) ? 'inbound_workflow_id'
          AND ((tc.credentials::jsonb)->>'inbound_workflow_id') ~ '^[0-9]+$'
          AND tpn.inbound_workflow_id IS NULL
        """
    )

    # 2. Strip the legacy key from ARI configs' credentials so the schema and
    #    the data agree (the ARI provider's request/response models no longer
    #    declare inbound_workflow_id).
    op.execute(
        """
        UPDATE telephony_configurations
        SET credentials = ((credentials::jsonb) - 'inbound_workflow_id')::json,
            updated_at = NOW()
        WHERE provider = 'ari'
          AND (credentials::jsonb) ? 'inbound_workflow_id'
        """
    )


def downgrade() -> None:
    # Best-effort reverse: pick one inbound_workflow_id per ARI config from its
    # linked phone numbers and stuff it back into credentials. Not perfect when
    # extensions had different workflows in the new model — picks the smallest
    # phone-number id with a non-null workflow id.
    op.execute(
        """
        UPDATE telephony_configurations tc
        SET credentials = jsonb_set(
            (tc.credentials::jsonb),
            '{inbound_workflow_id}',
            to_jsonb(sub.inbound_workflow_id)
        )::json,
            updated_at = NOW()
        FROM (
            SELECT DISTINCT ON (telephony_configuration_id)
                telephony_configuration_id,
                inbound_workflow_id
            FROM telephony_phone_numbers
            WHERE inbound_workflow_id IS NOT NULL
            ORDER BY telephony_configuration_id, id
        ) sub
        WHERE tc.id = sub.telephony_configuration_id
          AND tc.provider = 'ari'
        """
    )
