import datetime as dt
from sqlalchemy.orm import Session
from sqlalchemy import select

from models import Teacher, Leave, Substitution


# ====================================================
# CREAR BAJA
# ====================================================

def create_leave(teacher_id: int, start_date: str, db: Session):
    date = dt.date.fromisoformat(start_date)
    leave = Leave(
        teacher_id=teacher_id,
        start_date=date,
        end_date=None,
    )
    db.add(leave)
    db.commit()


# ====================================================
# LISTAR BAJAS ABIERTAS
# ====================================================

def get_open_leaves(db: Session):
    return db.execute(select(Leave).where(Leave.end_date == None)).scalars().all()


# ====================================================
# CREA SUSTITUCIÓN
# ====================================================

def create_substitution(leave_id: int, start_date: str, name: str, email: str, db: Session):
    date = dt.date.fromisoformat(start_date)

    # Crear o encontrar profesor sustituto
    email = email.lower().strip()
    teacher = db.execute(select(Teacher).where(Teacher.email == email)).scalar_one_or_none()
    if teacher:
        teacher.is_current = True
        teacher.name = name
    else:
        teacher = Teacher(
            name=name.strip(),
            email=email,
            is_current=True
        )
        db.add(teacher)
        db.commit()

    # Crear registro de sustitución
    sub = Substitution(
        leave_id=leave_id,
        substitute_teacher_id=teacher.id,
        start_date=date,
        end_date=None,
    )
    db.add(sub)
    db.commit()


# ====================================================
# CERRAR BAJA
# ====================================================

def close_leave(leave_id: int, end_date: str, db: Session):
    date = dt.date.fromisoformat(end_date)

    leave = db.get(Leave, leave_id)
    if not leave:
        return

    leave.end_date = date

    # También finalizar sustitución activa
    sub = db.execute(
        select(Substitution).where(Substitution.leave_id == leave_id, Substitution.end_date == None)
    ).scalar_one_or_none()

    if sub:
        sub.end_date = date
        # El sustituto deja de estar activo en el centro (pero NO se borra)
        teacher = db.get(Teacher, sub.substitute_teacher_id)
        if teacher:
            teacher.is_current = False

    db.commit()