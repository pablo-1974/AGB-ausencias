"""cleanup teacher active and ensure status

Revision ID: eed2caf76ca5
Revises: 15ecdc153415
Create Date: 2026-03-10 06:28:40.412949
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "eed2caf76ca5"
down_revision: Union[str, None] = "15ecdc153415"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 1) Asegurar que el tipo enum existe
    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.create(bind, checkfirst=True)

    cols = [c["name"] for c in insp.get_columns("teachers")]

    # 2) Añadir 'status' si no existe (server_default para poblar datos existentes)
    if "status" not in cols:
        op.add_column(
            "teachers",
            sa.Column("status", teacher_status, nullable=False, server_default="activo"),
        )

        # 3) Si existe 'active', migrar sus valores a 'status'
        if "active" in cols:
            bind.execute(
                sa.text(
                    """
                    UPDATE teachers
                       SET status = CASE
                                      WHEN active IS TRUE  THEN 'activo'
                                      WHEN active IS FALSE THEN 'baja'
                                      ELSE 'activo'
                                    END
                    """
                )
            )

        # 4) Quitar el server_default del esquema
        op.alter_column("teachers", "status", server_default=None)

    # 5) Eliminar 'active' si sigue existiendo
    if "active" in cols:
        op.drop_column("teachers", "active")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = [c["name"] for c in insp.get_columns("teachers")]

    # 1) Restaurar 'active' si no existe
    if "active" not in cols:
        op.add_column(
            "teachers",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        )
        # Si tenemos 'status', mapear a active: activo=True; baja/excedencia=False
        if "status" in cols:
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

    # 2) Borrar 'status' si existe
    if "status" in cols:
        op.drop_column("teachers", "status")

    # 3) Borrar enum (si ya no hay dependencias)
    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.drop(bind, checkfirst=True)
