"""teacher status enum

Revision ID: 15ecdc153415
Revises: 288071e26419
Create Date: 2026-03-10 05:52:33.434971
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "15ecdc153415"
down_revision: Union[str, None] = "288071e26419"  # <-- asegúrate de que es tu última revisión real
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) Crear el tipo enum en Postgres (si no existiera)
    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.create(bind, checkfirst=True)

    # 2) Añadir columna 'status' con default 'activo'
    op.add_column(
        "teachers",
        sa.Column("status", teacher_status, nullable=False, server_default="activo"),
    )

    # 3) Migrar datos desde 'active' si existiese
    cols = [c["name"] for c in insp.get_columns("teachers")]
    if "active" in cols:
        bind.execute(
            sa.text(
                """
                UPDATE teachers
                   SET status = CASE
                                  WHEN active IS TRUE THEN 'activo'
                                  WHEN active IS FALSE THEN 'baja'
                                  ELSE 'activo'
                                END
                """
            )
        )

    # 4) Quitar el server_default del esquema (dejamos el modelo con default app-side)
    op.alter_column("teachers", "status", server_default=None)

    # 5) (Opcional) Eliminar columna 'active'
    if "active" in cols:
        op.drop_column("teachers", "active")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = [c["name"] for c in insp.get_columns("teachers")]

    # 1) (Opcional) restaurar 'active' como boolean con default TRUE
    if "active" not in cols:
        op.add_column(
            "teachers",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        )
        # Mapear status -> active (activo=True; baja/excedencia=False)
        bind.execute(
            sa.text(
                """
                UPDATE teachers
                   SET active = CASE
                                   WHEN status = 'activo' THEN TRUE
                                   ELSE FALSE
                                 END
                """
            )
        )
        op.alter_column("teachers", "active", server_default=None)

    # 2) Eliminar columna 'status' y el tipo enum
    if "status" in cols:
        op.drop_column("teachers", "status")

    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.drop(bind, checkfirst=True)
