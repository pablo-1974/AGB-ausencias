"""add alias to teachers

Revision ID: 288071e26419
Revises: 840d22bcafac
Create Date: 2026-03-08 17:11:56.296753

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "288071e26419"
down_revision: Union[str, None] = "840d22bcafac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("teachers", sa.Column("alias", sa.String(length=120), nullable=True))
    op.create_index("ix_teachers_alias", "teachers", ["alias"], unique=False)

def downgrade() -> None:
    op.drop_index("ix_teachers_alias", table_name="teachers")
    op.drop_column("teachers", "alias")

