# models.py
from __future__ import annotations

# SQLAlchemy base y utilidades
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
from sqlalchemy import (
    String, Integer, Enum as SAEnum, ForeignKey, Text, Date, Boolean, UniqueConstraint
)
import enum
from datetime import datetime


# ---------------------------------------------------------
# Base
# ---------------------------------------------------------
class Base(DeclarativeBase):
    """
    Base declarativa de SQLAlchemy para todos los modelos.
    """
    pass


# ---------------------------------------------------------
# Enums (aplicación)
# ---------------------------------------------------------
class Role(str, enum.Enum):
    """
    Rol de usuario en la aplicación.
    """
    admin = "admin"
    user = "user"


class ScheduleType(str, enum.Enum):
    """
    Tipos de 'slots' de horario:
    - CLASS: clase (grupo, aula, materia)
    - GUARD: guardia (guard_type textual)
    """
    CLASS = "CLASS"
    GUARD = "GUARD"


class GuardType(str, enum.Enum):
    """
    Etiquetas de guardia admitidas (si deseas tiparlas).
    """
    G_AULA = "G AULA"
    G_RECREO_PATIO = "G RECREO PATIO"
    G_RECREO_PASILLO = "G RECREO PASILLO"


class TeacherStatus(str, enum.Enum):
    """
    Estado del profesor en el centro:
    - activo: está en el centro en activo
    - baja: en situación de baja
    - excedencia: en situación de excedencia
    - exprofe: ex-profesor (histórico, no operativo)
    """
    activo = "activo"
    baja = "baja"
    excedencia = "excedencia"
    exprofe = "exprofe"

# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------
class User(Base):
    """
    Usuarios de la aplicación (login).
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.user)
    active: Mapped[bool] = mapped_column(default=True)  # estado del usuario (no confundir con Teacher.status)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Teacher(Base):
    """
    Profesores del centro. Sustituye el booleano 'active' por 'status' (enum).
    """
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    alias: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # ⬇️ NUEVO: estado del profesor (enum) en lugar de 'active: bool'
    status: Mapped[TeacherStatus] = mapped_column(
        SAEnum(TeacherStatus, name="teacher_status"),  # nombre del tipo enum en BD
        nullable=False,
        default=TeacherStatus.activo
    )

    __table_args__ = (
        UniqueConstraint("name", "email", name="uq_teacher_name_email"),
    )


class ScheduleSlot(Base):
    """
    Slot de horario de un profesor.

    - type = CLASS → usar (group, room, subject)
    - type = GUARD → usar (guard_type)
    """
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"),
        index=True
    )

    # 0=Lunes...4=Viernes
    day_index: Mapped[int] = mapped_column(Integer)
    # 0=1ª ... 6=6ª ; recreo el que corresponda (normalmente índice 3)
    hour_index: Mapped[int] = mapped_column(Integer)

    type: Mapped[ScheduleType] = mapped_column(SAEnum(ScheduleType))

    # SOLO si type=GUARD
    guard_type: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # SOLO si type=CLASS
    group: Mapped[str | None] = mapped_column(String(50))
    room: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str | None] = mapped_column(String(50))

    # Fuente del dato (import/manual/etc.)
    source: Mapped[str | None] = mapped_column(String(30), default="import")


class Leave(Base):
    """
    Bajas registradas:
    - Cuando NO hay sustituto: el profesor ausente aparece como tal en parte diario.
    - Cuando hay sustituto: el ausente NO aparece en parte diario (lo sustituye otro).
    """
    __tablename__ = "leaves"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"),
        index=True
    )
    start_date: Mapped[Date] = mapped_column(Date)
    end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    cause: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    
    # Profesor sustituto (si lo hay) mientras dure la baja.
    substitute_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id"),
        nullable=True
    )
    
    # fechas de sustitución
    substitute_start_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    substitute_end_date:   Mapped[Date | None] = mapped_column(Date, nullable=True)


class Absence(Base):
    """
    Ausencias puntuales (por día y franja).
    hours_mask:
      bit0 = 1ª
      bit1 = 2ª
      ...
      bit5 = 6ª
    (Recreo no se marca normalmente en mask)
    """
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"),
        index=True
    )
    date: Mapped[Date] = mapped_column(Date, index=True)
    hours_mask: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    # Categoría (A..L o Z o None)
    category: Mapped[str | None] = mapped_column(String(2))

    __table_args__ = (
        UniqueConstraint("teacher_id", "date", name="uq_teacher_date"),
    )


# ---------------------------------------------------------
# (Opcional) Modelo de sustituciones explícitas
# ---------------------------------------------------------
# Si decides llevar un registro explícito de sustituciones actuales (substitute -> replaced)
# en vez de (o además de) Leave, puedes usar un modelo como este:
#
# class Substitution(Base):
#     __tablename__ = "substitutions"
#
#     id: Mapped[int] = mapped_column(primary_key=True)
#     substitute_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True, nullable=False)
#     replaced_id:   Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True, nullable=False)
#     start_date:    Mapped[Date | None] = mapped_column(Date, nullable=True)
#     end_date:      Mapped[Date | None] = mapped_column(Date, nullable=True)
#
#     # Relaciones opcionales (si te interesa navegar)
#     # substitute = relationship("Teacher", foreign_keys=[substitute_id])
#     # replaced   = relationship("Teacher", foreign_keys=[replaced_id])
#
# NOTA: si usas este modelo, recuerda crear su migración Alembic y adaptar los routers
# que lean sustituciones (por ejemplo, /teachers/list “Profesorado Actual”).



