"""teacher status enum

Revision ID: 15ecdc153415
Revises: 288071e26419
Create Date: 2026-03-10 05:52:33.434971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15ecdc153415'
down_revision: Union[str, None] = '288071e26419'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
