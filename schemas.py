from pydantic import BaseModel
from datetime import date

class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class AbsenceCreate(BaseModel):
    teacher_id: int
    date: date
    hours_mask: int
    explanation: str