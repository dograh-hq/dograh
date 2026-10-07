"""merge main and whatsapp outbound migrations

Revision ID: b3ddd69c9301
Revises: 91a84e0a1947, c3f5a1b90d47
Create Date: 2026-10-07 14:54:13.022744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3ddd69c9301'
down_revision: Union[str, None] = ('91a84e0a1947', 'c3f5a1b90d47')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
