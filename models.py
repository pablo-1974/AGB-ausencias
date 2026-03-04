from sqlalchemy import (
    Column, Integer, String, Boolean, Date, DateTime, ForeignKey, Enum, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from database import Base

class Role(enum.Enum):
    admin = "admin"
    user = "user"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    password_hash = Column(String)
    role = Column(Enum(Role), default=Role.user)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Teacher(Base):
    __tablename__ = "teachers"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True)
    is_current = Column(Boolean, default=True)

class DutyType(enum.Enum):
    AULA = "G AULA"
    RECREO = "G RECREO"

class GuardDuty(Base):
    __tablename__ = "guard_duties"
    __table_args__ = (
        UniqueConstraint("teacher_id", "weekday", "slot", name="uq_teacher_weekday_slot"),
    )

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    weekday = Column(Integer)  # 0..4
    slot = Column(String)      # "1","2","3","RECREO","4","5","6"
    type = Column(Enum(DutyType))

class TeachingSlot(Base):
    __tablename__ = "teaching_slots"

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    weekday = Column(Integer)
    slot = Column(String)
    group = Column(String)
    room = Column(String)
    subject = Column(String)

class Leave(Base):
    __tablename__ = "leaves"

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    start_date = Column(Date)
    end_date = Column(Date, nullable=True)

class Substitution(Base):
    __tablename__ = "substitutions"

    id = Column(Integer, primary_key=True)
    leave_id = Column(Integer, ForeignKey("leaves.id"))
    substitute_teacher_id = Column(Integer, ForeignKey("teachers.id"))
    start_date = Column(Date)
    end_date = Column(Date, nullable=True)

class AbsenceCategory(enum.Enum):
    A="A"; B="B"; C="C"; D="D"; E="E"; F="F"; G="G"; H="H"; I="I"; J="J"; K="K"; L="L"; Z="Z"

class Absence(Base):
    __tablename__ = "absences"

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    date = Column(Date)
    hours_mask = Column(Integer)  # bits 0–6
    explanation = Column(String)
    category = Column(Enum(AbsenceCategory), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)