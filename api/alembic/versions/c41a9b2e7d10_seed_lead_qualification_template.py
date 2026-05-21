"""seed lead qualification workflow template

Idempotently inserts the built-in starter templates defined in
``api.services.workflow.seed_templates`` into ``workflow_templates``.

Self-hosted users can then create a working voice agent from the template
without needing the hosted MPS workflow generator. The migration is safe
to run repeatedly: it skips rows whose ``template_name`` already exists.

Revision ID: c41a9b2e7d10
Revises: 4c1f1e3e8ef2
Create Date: 2026-05-16 18:50:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from api.services.workflow.seed_templates import BUILTIN_TEMPLATES

# revision identifiers, used by Alembic.
revision: str = "c41a9b2e7d10"
down_revision: Union[str, None] = "4c1f1e3e8ef2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    for tmpl in BUILTIN_TEMPLATES:
        existing = bind.execute(
            sa.text(
                "SELECT 1 FROM workflow_templates WHERE template_name = :name LIMIT 1"
            ),
            {"name": tmpl["template_name"]},
        ).first()
        if existing:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO workflow_templates "
                "(template_name, template_description, template_json, created_at) "
                "VALUES (:name, :description, CAST(:json AS JSON), NOW())"
            ),
            {
                "name": tmpl["template_name"],
                "description": tmpl["template_description"],
                "json": json.dumps(tmpl["template_json"]),
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    for tmpl in BUILTIN_TEMPLATES:
        bind.execute(
            sa.text("DELETE FROM workflow_templates WHERE template_name = :name"),
            {"name": tmpl["template_name"]},
        )
