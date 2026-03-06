# schemas.py
from datetime import date
from pydantic import BaseModel, EmailStr
from typing import Optional
from models import Role, ScheduleType


# ---------------------------
# USERS
# ---------------------------

class UserBase(BaseModel):
    name: str
    email: EmailStr


class UserCreate(UserBase):
    password: str


class UserOut(UserBase):
    id: int
    role: Role
    active: bool

    class Config:
        orm_mode = True


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class PasswordReset(BaseModel):
    new_password: str


# ---------------------------
# TEACHERS
# ---------------------------

class TeacherBase(BaseModel):
    name: str
    email: EmailStr


class TeacherCreate(TeacherBase):
    pass


class TeacherOut(TeacherBase):
    id: int
    active: bool

    class Config:
        orm_mode = True


# ---------------------------
# SCHEDULE
# ---------------------------

class ScheduleSlotBase(BaseModel):
    day_index: int
    hour_index: int
    type: ScheduleType


class ScheduleSlotClass(BaseModel):
    group: Optional[str]
    room: Optional[str]
    subject: Optional[str]


class ScheduleSlotGuard(BaseModel):
    guard_type: Optional[str]  # “G AULA”, “G RECREO PATIO”, etc.


class ScheduleSlotOut(BaseModel):
    id: int
    teacher_id: int
    day_index: int
    hour_index: int
    type: ScheduleType
    group: Optional[str]
    room: Optional[str]
    subject: Optional[str]
    guard_type: Optional[str]

    class Config:
        orm_mode = True


# ---------------------------
# ABSENCES
# ---------------------------

class AbsenceCreate(BaseModel):
    teacher_id: int
    date: date
    hours_mask: int
    note: Optional[str] = None


class AbsenceOut(BaseModel):
    id: int
    teacher_id: int
    date: date
    hours_mask: int
    note: Optional[str]
    category: Optional[str]

    class Config:
        orm_mode = True


# ---------------------------
# LEAVES
# ---------------------------

class LeaveCreate(BaseModel):
    teacher_id: int
    start_date: date


class LeaveClose(BaseModel):
    teacher_id: int
    end_date: date


class SubstitutionCreate(BaseModel):
    teacher_id: int            # profesor de baja
    start_date: date           # inicio sustitución
    substitute_name: str       # nombre del sustituto si no existe
