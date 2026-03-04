from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from io import BytesIO
import datetime as dt

from sqlalchemy.orm import Session
from sqlalchemy import select

from models import Absence, Teacher, AbsenceCategory


# ====================================================
# AGRUPAR AUSENCIAS
# ====================================================

def group_absences(start: dt.date, end: dt.date, db: Session):
    absences = db.execute(
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(
            Absence.date >= start,
            Absence.date <= end,
            Absence.category != AbsenceCategory.Z,
            Absence.category != None
        )
        .order_by(Absence.teacher_id, Absence.category, Absence.date)
    ).all()

    grouped = []
    if not absences:
        return grouped

    # Fusionar días consecutivos
    current = None
    for abs_obj, teacher in absences:
        if current is None:
            current = {
                "teacher": teacher.name,
                "category": abs_obj.category.value,
                "start": abs_obj.date,
                "end": abs_obj.date,
                "hours_mask": abs_obj.hours_mask
            }
            continue

        if (
            teacher.name == current["teacher"]
            and abs_obj.category.value == current["category"]
            and abs_obj.date == current["end"] + dt.timedelta(days=1)
        ):
            current["end"] = abs_obj.date
        else:
            grouped.append(current)
            current = {
                "teacher": teacher.name,
                "category": abs_obj.category.value,
                "start": abs_obj.date,
                "end": abs_obj.date,
                "hours_mask": abs_obj.hours_mask
            }

    grouped.append(current)
    return grouped


# ====================================================
# GENERAR PDF
# ====================================================

def generate_monthly_pdf(start_str: str, end_str: str, db: Session):
    start = dt.date.fromisoformat(start_str)
    end = dt.date.fromisoformat(end_str)

    data = group_absences(start, end, db)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    W, H = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(20*mm, H - 20*mm, "PARTE MENSUAL DE AUSENCIAS")

    y = H - 30*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, y, f"Desde: {start.strftime('%d/%m/%Y')}   Hasta: {end.strftime('%d/%m/%Y')}")
    y -= 10*mm

    # Cabeceras
    headers = ["Nombre", "Fecha", "Horas", "Causa", "Inicio", "Fin", "Días"]
    colw = [40*mm, 25*mm, 15*mm, 15*mm, 25*mm, 25*mm, 15*mm]
    c.setFont("Helvetica-Bold", 8)

    x = 10*mm
    for i, hname in enumerate(headers):
        c.drawString(x, y, hname)
        x += colw[i]

    y -= 8*mm
    c.setFont("Helvetica", 8)

    for row in data:
        x = 10*mm
        startd = row["start"]
        endd = row["end"]
        days = (endd - startd).days + 1

        fields = [
            row["teacher"],
            startd.strftime("%d/%m/%Y") if startd == endd else f"{startd:%d/%m} - {endd:%d/%m}",
            str(bin(row["hours_mask"]).count("1")),
            row["category"],
            startd.strftime("%d/%m/%Y"),
            endd.strftime("%d/%m/%Y"),
            str(days)
        ]

        for i, val in enumerate(fields):
            c.drawString(x, y, val)
            x += colw[i]

        y -= 6*mm
        if y < 20*mm:
            c.showPage()
            y = H - 20*mm

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer