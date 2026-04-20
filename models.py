# models.py
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy import (
    String, Integer, Enum as SAEnum, ForeignKey, Text,
    Date, Boolean, UniqueConstraint, DateTime, JSON, Column
)

from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

import enum
from datetime import datetime


# ---------------------------------------------------------
# Base
# ---------------------------------------------------------
class Base(DeclarativeBase):
    """Base declarativa común para todos los modelos."""
    pass


# ---------------------------------------------------------
# Enums de la aplicación
# ---------------------------------------------------------
class Role(str, enum.Enum):
    admin = "admin"
    user = "user"


class ScheduleType(str, enum.Enum):
    CLASS = "CLASS"
    GUARD = "GUARD"


class TeacherStatus(str, enum.Enum):
    activo = "activo"
    baja = "baja"
    excedencia = "excedencia"
    exprofe = "exprofe"


# ---------------------------------------------------------
# MODELO User
# ---------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.user)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# ---------------------------------------------------------
# MODELO Teacher
# ---------------------------------------------------------
class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str] = mapped_column(String(190), unique=True, index=True)
    alias: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # True si es profesor titular
    titular: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    status: Mapped[TeacherStatus] = mapped_column(
        SAEnum(TeacherStatus, name="teacher_status"),
        nullable=False,
        default=TeacherStatus.activo
    )

    __table_args__ = (
        UniqueConstraint("name", "email", name="uq_teacher_name_email"),
    )


# ---------------------------------------------------------
# MODELO ScheduleSlot
# ---------------------------------------------------------
class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(primary_key=True)

    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"), index=True
    )

    day_index: Mapped[int] = mapped_column(Integer)   # 0 = lunes ... 4 = viernes
    hour_index: Mapped[int] = mapped_column(Integer)  # 0..6

    type: Mapped[ScheduleType] = mapped_column(SAEnum(ScheduleType))

    guard_type: Mapped[str | None] = mapped_column(String(40))

    group: Mapped[str | None] = mapped_column(String(50))
    room: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str | None] = mapped_column(String(50))

    source: Mapped[str | None] = mapped_column(String(30), default="import")


# ---------------------------------------------------------
# MODELO Leave  ✅ CON JERARQUÍA REAL
# ---------------------------------------------------------
class Leave(Base):
    """
    Bajas jerárquicas:
    - Una baja puede tener una baja hija (sustituto)
    - parent_leave_id controla la jerarquía real
    """
    __tablename__ = "leaves"

    id: Mapped[int] = mapped_column(primary_key=True)

    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"),
        index=True
    )

    # ✅ NUEVO: jerarquía de bajas
    parent_leave_id: Mapped[int | None] = mapped_column(
        ForeignKey("leaves.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    start_date: Mapped[Date] = mapped_column(Date)
    end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)

    cause: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Información opcional para vistas (ya no define la jerarquía)
    substitute_teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"))
    substitute_start_date: Mapped[Date | None] = mapped_column(Date)
    substitute_end_date: Mapped[Date | None] = mapped_column(Date)

    category: Mapped[str | None] = mapped_column(String(2))


    # ✅ NUEVO: distingue bajas reales de leaves técnicos de sustitución
    is_substitution: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )


# ---------------------------------------------------------
# MODELO Absence
# ---------------------------------------------------------
class Absence(Base):
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[Date] = mapped_column(Date, index=True)
    hours_mask: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(2))

    __table_args__ = (
        UniqueConstraint("teacher_id", "date", name="uq_teacher_date"),
    )


# ---------------------------------------------------------
# MODELO SchoolCalendar
# ---------------------------------------------------------
class SchoolCalendar(Base):
    __tablename__ = "school_calendar"

    id = Column(Integer, primary_key=True)
    school_year = Column(String, nullable=False)

    first_day = Column(Date, nullable=False)
    last_day = Column(Date, nullable=False)

    xmas_start = Column(Date, nullable=False)
    xmas_end = Column(Date, nullable=False)

    easter_start = Column(Date, nullable=False)
    easter_end = Column(Date, nullable=False)

    other_holidays = Column(JSON, default=list)

    updated_at = Column(DateTime, default=datetime.utcnow)

# ---------------------------------------------------------
# MODELO ActionLog
# ---------------------------------------------------------
class ActionLog(Base):
    """
    Registro de acciones administrativas del sistema.
    Una fila representa una acción relevante realizada por un usuario.
    """

    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Usuario que realiza la acción
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Tipo de acción (login, absence_create, leave_close, etc.)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Entidad afectada (teacher, absence, leave, user, calendar, backup...)
    entity: Mapped[str | None] = mapped_column(String(50), index=True)

    # ID concreto de la entidad afectada
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Descripción humana de la acción
    detail: Mapped[str | None] = mapped_column(Text)

    # Momento exacto de la acción
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True
    )

    # Relación opcional para vistas administrativas
    user = relationship("User", lazy="joined")
