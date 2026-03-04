from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from io import BytesIO
import datetime as dt

from sqlalchemy.orm import Session
from sqlalchemy import select

from models import Teacher, Absence, Leave, Substitution, GuardDuty, TeachingSlot


# ====================================================
# OBTENER AUSENTES DEL DÍA
# ====================================================

def get_absent_teachers(date: dt.date, db: Session):
    # Ausencias declaradas
    absents = db.execute(
        select(Absence.teacher_id).where(Absence.date == date)
    ).scalars().all()

    # Profesores de baja sin sustituto activo ese día
    open_leaves = db.execute(
        select(Leave).where(Leave.start_date <= date, (Leave.end_date == None) | (Leave.end_date >= date))
    ).scalars().all()

    for leave in open_leaves:
        # Ver si hay sustituto activo
        sub = db.execute(
            select(Substitution)
            .where(
                Substitution.leave_id == leave.id,
                Substitution.start_date <= date,
                (Substitution.end_date == None) | (Substitution.end_date >= date)
            )
        ).scalar_one_or_none()

        if not sub:
            absents.append(leave.teacher_id)

    return list(set(absents))


# ====================================================
# OBTENER GUARDIAS POR FRANJA
# ====================================================

def get_guardias_for_day(weekday: int, db: Session):
    guardias = db.execute(
        select(GuardDuty).where(GuardDuty.weekday == weekday)
    ).scalars().all()

    result = {}
    for gd in guardias:
        result.setdefault(gd.slot, [])
        teacher = db.get(Teacher, gd.teacher_id)
        result[gd.slot].append(teacher.name)
    return result


# ====================================================
# OBTENER CLASES (solo si hay)
# ====================================================

def get_classes_for_teacher(teacher_id, weekday, db: Session):
    rows = db.execute(
        select(TeachingSlot).where(
            TeachingSlot.teacher_id == teacher_id,
            TeachingSlot.weekday == weekday
        )
    ).scalars().all()

    out = {}
    for r in rows:
        out[r.slot] = {
            "grupo": r.group,
            "aula": r.room,
            "asignatura": r.subject
        }
    return out


# ====================================================
# GENERAR PDF DIARIO
# ====================================================

def generate_daily_pdf(date_str: str, observations: str, db: Session):
    date = dt.date.fromisoformat(date_str)
    weekday = date.weekday()

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    W, H = A4

    # Título
    title = f"AUSENCIAS DEL DÍA {date.strftime('%A %d/%m/%Y').upper()}"
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20*mm, H - 20*mm, title)

    # Observaciones
    c.setFont("Helvetica", 10)
    c.drawString(20*mm, H - 30*mm, "Observaciones:")
    c.rect(20*mm, H - 60*mm, W - 40*mm, 25*mm)

    if observations:
        c.drawString(22*mm, H - 35*mm, observations[:100])

    # Datos del día
    absents = get_absent_teachers(date, db)
    guardias = get_guardias_for_day(weekday, db)

    slots = ["1", "2", "3", "RECREO", "4", "5", "6"]

    y = H - 75*mm
    c.setFont("Helvetica-Bold", 9)

    # Cabecera
    headers = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    colw = [16*mm, 40*mm, 20*mm, 18*mm, 25*mm, 20*mm, 40*mm]

    x = 15*mm
    for i, h in enumerate(headers):
        c.drawString(x, y, h)
        x += colw[i]

    y -= 7*mm

    # Filas
    for slot in slots:
        x = 15*mm

        # Fila de recreo gris
        if slot == "RECREO":
            c.setFillGray(0.9)
            c.rect(15*mm, y - 4*mm, sum(colw), 8*mm, fill=1, stroke=0)
            c.setFillGray(0)

        c.setFont("Helvetica", 9)

        # Ausentes
        absent_list = []
        class_group = ""
        class_room = ""
        class_subj = ""

        for tid in absents:
            classes = get_classes_for_teacher(tid, weekday, db)
            if slot in classes:
                absent_list.append(db.get(Teacher, tid).name)
                class_group = classes[slot]["grupo"]
                class_room = classes[slot]["aula"]
                class_subj = classes[slot]["asignatura"]

        # Guardias
        guard_list = guardias.get(slot, [])

        fields = [
            slot,
            ", ".join(absent_list),
            class_group,
            class_room,
            class_subj,
            "",
            ", ".join(guard_list)
        ]

        for i, val in enumerate(fields):
            c.drawString(x, y, val)
            x += colw[i]

        y -= 12*mm

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer