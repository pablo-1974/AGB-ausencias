"""ensure teachers.status exists and drop active

Revision ID: fbaae25659e5
Revises: eed2caf76ca5
Create Date: 2026-03-10 18:53:31.603760

"""

from alembic import op
import sqlalchemy as sa

# Identificadores de la revisión
revision = "fix_add_status_after_empty"
down_revision = "eed2caf76ca5"   # <- VA ENCIMA DE TU HEAD ACTUAL
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Asegurar el tipo ENUM en Postgres (no falla si ya existe)
    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.create(bind, checkfirst=True)

    cols = [c["name"] for c in insp.get_columns("teachers")]

    # Añadir 'status' si no existe; default 'activo' para rellenar filas existentes
    if "status" not in cols:
        op.add_column(
            "teachers",
            sa.Column("status", teacher_status, nullable=False, server_default="activo"),
        )

        # Si existiera 'active', migrar los valores a 'status'
        if "active" in cols:
            bind.execute(sa.text("""
                UPDATE teachers
                   SET status = CASE
                                  WHEN active IS TRUE  THEN 'activo'
                                  WHEN active IS FALSE THEN 'baja'
                                  ELSE 'activo'
                                END
            """))

        # Quitar el default del esquema (lo gestiona el modelo)
        op.alter_column("teachers", "status", server_default=None)

    # Eliminar 'active' si todavía existe
    if "active" in cols:
        op.drop_column("teachers", "active")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = [c["name"] for c in insp.get_columns("teachers")]

    # Restaurar 'active' si no existe
    if "active" not in cols:
        op.add_column(
            "teachers",
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        )
        # Si 'status' existe, mapear a active (activo=True; resto=False)
        if "status" in cols:
            bind.execute(sa.text("""
                UPDATE teachers
                   SET active = CASE
                                  WHEN status = 'activo' THEN TRUE
                                  ELSE FALSE
                                END
            """))
        op.alter_column("teachers", "active", server_default=None)

    # Borrar 'status' si existe
    if "status" in cols:
        op.drop_column("teachers", "status")

    # Borrar ENUM (si no hay dependencia)
    teacher_status = sa.Enum("activo", "baja", "excedencia", name="teacher_status")
    teacher_status.drop(bind, checkfirst=True)
