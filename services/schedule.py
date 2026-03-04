from sqlalchemy.orm import Session
from sqlalchemy import select
from models import TeachingSlot, GuardDuty, Teacher


# ====================================================
# OBTENER HORARIO DE UN PROFESOR
# ====================================================

def get_schedule_for_teacher(teacher_id: int, db: Session):
    teacher = db.get(Teacher, teacher_id)
    if not teacher:
        return None

    # Obtenemos clases y guardias
    classes = db.execute(
        select(TeachingSlot).where(TeachingSlot.teacher_id == teacher_id)
    ).scalars().all()

    guardias = db.execute(
        select(GuardDuty).where(GuardDuty.teacher_id == teacher_id)
    ).scalars().all()

    # Reorganizar por día/slot
    schedule = {}
    for slot in classes:
        schedule.setdefault(slot.weekday, {})
        schedule[slot.weekday][slot.slot] = {
            "tipo": "clase",
            "grupo": slot.group,
            "aula": slot.room,
            "materia": slot.subject,
        }

    for gd in guardias:
        schedule.setdefault(gd.weekday, {})
        schedule[gd.weekday][gd.slot] = {
            "tipo": "guardia",
            "grupo": "",
            "aula": "",
            "materia": gd.type.value,
        }

    # Devolver horario ordenado
    return {
        "teacher": teacher,
        "schedule": schedule
    }