import datetime as dt
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from models import Absence, Teacher, AbsenceCategory
from utils import hours_list_to_mask


# ====================================================
# CREAR AUSENCIA
# ====================================================

def create_absence(teacher_id, date_str, hours_str, explanation, user_id, db: Session):
    date = dt.date.fromisoformat(date_str)

    if hours_str == "Todas":
        mask = 127
    else:
        hours = hours_str.split(",")
        mask = hours_list_to_mask(hours)

    abs_obj = Absence(
        teacher_id=teacher_id,
        date=date,
        hours_mask=mask,
        explanation=explanation.strip(),
        created_by_user_id=user_id,
    )
    db.add(abs_obj)
    db.commit()


# ====================================================
# LISTAR AUSENCIAS DE UNA FECHA
# ====================================================

def get_absences_for_date(date_str, db: Session):
    date = dt.date.fromisoformat(date_str)
    absences = db.execute(
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(Absence.date == date)
    ).all()
    return absences


# ====================================================
# CATALOGAR AUSENCIA
# ====================================================

def categorize_absence(absence_id, category, db: Session):
    abs_obj = db.get(Absence, absence_id)
    if not abs_obj:
        return
    if category not in AbsenceCategory.__members__:
        return
    abs_obj.category = AbsenceCategory[category]
    db.commit()


# ====================================================
# BORRAR AUSENCIA
# ====================================================

def delete_absence(absence_id, db: Session):
    db.execute(delete(Absence).where(Absence.id == absence_id))
    db.commit()


# ====================================================
# AUSENCIAS SIN CATALOGAR
# ====================================================

def get_uncategorized_absences(db: Session):
    return db.execute(
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(Absence.category == None)
    ).all()