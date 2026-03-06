# models.py
from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
from sqlalchemy import String, Integer, Enum, ForeignKey, Text, Date, Boolean, UniqueConstraint
import enum
from datetime import datetime


# ---------------------------------------------------------
# Base
# ---------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------
# Enums
# ---------------------------------------------------------
class Role(str, enum.Enum):
    admin = "admin"
    user = "user"


class ScheduleType(str, enum.Enum):
    CLASS = "CLASS"
    GUARD = "GUARD"


class GuardType(str, enum.Enum):
    G_AULA = "G AULA"
    G_RECREO_PATIO = "G RECREO PATIO"
    G_RECREO_PASILLO = "G RECREO PASILLO"


# ---------------------------------------------------------
# MODELOS
# ---------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (
        UniqueConstraint("name", "email", name="uq_teacher_name_email"),
    )


class ScheduleSlot(Base):
    """
    Slot de horario de profesor:
    - type = CLASS → grupo/aula/asignatura
    - type = GUARD → guard_type = GuardType
    """
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)

    day_index: Mapped[int] = mapped_column(Integer)    # 0=Lunes...4=Viernes
    hour_index: Mapped[int] = mapped_column(Integer)   # 0=1ª ... 6=6ª ; recreo el que corresponda

    type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType))

    # SOLO si type=GUARD
    guard_type: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # SOLO si type=CLASS
    group: Mapped[str | None] = mapped_column(String(50))
    room: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str | None] = mapped_column(String(50))

    source: Mapped[str | None] = mapped_column(String(30), default="import")


class Leave(Base):
    """
    Bajas.
    - Cuando no hay sustituto: profesor ausente todo el día en el parte diario.
    - Cuando tiene sustituto: el ausente NO aparece en parte diario.
    """
    __tablename__ = "leaves"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[Date] = mapped_column(Date)
    end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)

    substitute_teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True)


class Absence(Base):
    """
    Ausencias puntuales.
    hours_mask:
      bit0 = 1ª
      bit1 = 2ª
      ...
      bit5 = 6ª
    (Recreo no se marca normalmente en mask)
    """
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id", ondelete="CASCADE"), index=True)
    date: Mapped[Date] = mapped_column(Date, index=True)
    hours_mask: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(2))  # A..L o Z o None

    __table_args__ = (
        UniqueConstraint("teacher_id", "date", name="uq_teacher_date"),
    )
