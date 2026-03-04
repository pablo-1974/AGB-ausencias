import openpyxl
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from models import Teacher, GuardDuty, TeachingSlot, DutyType


# ============================================================
# IMPORTAR LISTADO DE PROFESORES
# ============================================================

async def import_teachers_file(upload_file, db: Session):
    wb = openpyxl.load_workbook(upload_file.file, data_only=True)
    ws = wb.active

    # Se espera: nombre | email
    for row in ws.iter_rows(min_row=2, values_only=True):
        name, email = row
        if not name or not email:
            continue

        email = str(email).lower().strip()

        teacher = db.execute(select(Teacher).where(Teacher.email == email)).scalar_one_or_none()

        if teacher:
            teacher.name = name
            teacher.is_current = True
        else:
            teacher = Teacher(name=name, email=email, is_current=True)
            db.add(teacher)

    db.commit()


# ============================================================
# IMPORTAR HORARIO (CLASES + GUARDIAS) — SIN REUNIONES
# ============================================================

async def import_schedule_file(upload_file, db: Session):
    wb = openpyxl.load_workbook(upload_file.file, data_only=True)
    ws = wb.active

    # Borrar datos antiguos
    db.execute(delete(GuardDuty))
    db.execute(delete(TeachingSlot))
    db.commit()

    daymap = {
        "lunes": 0, "martes": 1,
        "miércoles": 2, "miercoles": 2,
        "jueves": 3, "viernes": 4
    }

    def normalize_slot(s):
        s = str(s).strip().lower()
        mapping = {
            "1ª": "1", "1": "1",
            "2ª": "2", "2": "2",
            "3ª": "3", "3": "3",
            "recreo": "RECREO",
            "4ª": "4", "4": "4",
            "5ª": "5", "5": "5",
            "6ª": "6", "6": "6",
        }
        return mapping.get(s, s.upper())

    for row in ws.iter_rows(min_row=2, values_only=True):
        name, day, slot, group, room, subject = row

        if not name or not day or not slot:
            continue

        teacher = db.execute(
            select(Teacher).where(Teacher.name == str(name).strip())
        ).scalar_one_or_none()

        if not teacher:
            continue

        weekday = daymap.get(str(day).lower().strip())
        if weekday is None:
            continue

        slot = normalize_slot(slot)

        # Guardias
        if str(group).upper().strip() in ("G AULA", "G RECREO"):
            duty_type = DutyType.AULA if group.upper().strip() == "G AULA" else DutyType.RECREO
            db.add(GuardDuty(
                teacher_id=teacher.id,
                weekday=weekday,
                slot=slot,
                type=duty_type
            ))
            continue

        # Clases normales
        db.add(TeachingSlot(
            teacher_id=teacher.id,
            weekday=weekday,
            slot=slot,
            group=str(group).strip() if group else "",
            room=str(room).strip() if room else "",
            subject=str(subject).strip() if subject else ""
        ))

    db.commit()