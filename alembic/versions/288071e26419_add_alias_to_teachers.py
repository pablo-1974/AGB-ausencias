from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "288071e26419"          # <-- PON aquí el ID del archivo
down_revision = "840d22bcafac"     # <-- tu migración inicial
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("teachers", sa.Column("alias", sa.String(length=120), nullable=True))
    op.create_index("ix_teachers_alias", "teachers", ["alias"], unique=False)

def downgrade() -> None:
    op.drop_index("ix_teachers_alias", table_name="teachers")
    op.drop_column("teachers", "alias")
