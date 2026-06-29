"""merge saas + dograh 1.38 heads

Revision ID: 754feb4556ce
Revises: 91cc6ba3e1c7, e9c4a7b2f1d8
Create Date: 2026-06-29 19:27:23.616547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '754feb4556ce'
down_revision: Union[str, None] = ('91cc6ba3e1c7', 'e9c4a7b2f1d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
