# services/pdf_monthly.py
from __future__ import annotations
from typing import List, Dict, Tuple
from datetime import date, timedelta
from calendar import monthrange
import os

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import mm

from models import Absence, Leave, Teacher, ScheduleSlot, SchoolCalendar
from utils import mask_to_hour_list
from config import settings


# ---------------------------------------------------------
# AUX: Formato de días consecutivos
# ---------------------------------------------------------
def _format_date_span(dates: List[date]) -> Tuple[str, int]:
    if not dates:
        return "", 0
    dates = sorted(dates)
    d0, d1 = dates[0], dates[-1]
    if d0 == d1:
        return d0.strftime("%d/%m/%Y"), 1
    return (
        f"del {d0.strftime('%d/%m/%Y')} al {d1.strftime('%d/%m/%Y')}",
        (d1 - d0).days + 1,
    )


# ---------------------------------------------------------
# AUX: ¿El profesor trabaja este día?
# ---------------------------------------------------------
async def professor_works_day(session: AsyncSession, teacher_id: int, day: date) -> bool:
    """
    Devuelve True si el profesor tiene horario ese día,
    según ScheduleSlot.day_index.
    """
    weekday = day.weekday()      # 0=Lun ... 6=Dom
    if weekday >= 5:             # sábado/domingo
        return False

    res = await session.execute(
        select(ScheduleSlot.id).where(
            ScheduleSlot.teacher_id == teacher_id,
            ScheduleSlot.day_index == weekday
        )
    )
    return res.first() is not None


# ---------------------------------------------------------
# AUX: ¿Es festivo o vacaciones?
# ---------------------------------------------------------
def is_holiday(day: date, cal: SchoolCalendar) -> bool:
    """True si el día está fuera del curso o en vacaciones o festivos sueltos."""
    if day < cal.first_day or day > cal.last_day:
        return True

    if cal.xmas_start <= day <= cal.xmas_end:
        return True

    if cal.easter_start <= day <= cal.easter_end:
        return True

    if isinstance(cal.other_holidays, list):
        if day.isoformat() in cal.other_holidays:
            return True

    return False


# ---------------------------------------------------------
# AUX: Días lectivos reales
# ---------------------------------------------------------
async def working_school_days(
    session: AsyncSession,
    cal: SchoolCalendar,
    teacher_id: int,
    start: date,
    end: date,
) -> int:
    """
    Días lectivos reales:

    ✔ Lunes–Viernes
    ✔ No festivos
    ✔ No vacaciones
    ✔ No días fuera de curso
    ✔ Y SOLO días en los que el profesor tiene horario
    """
    cur = start
    count = 0

    while cur <= end:
        weekday = cur.weekday()

        # 1) Excluir sábado y domingo
        if weekday >= 5:
            cur += timedelta(days=1)
            continue

        # 2) Excluir festivos / vacaciones / fuera de curso
        if is_holiday(cur, cal):
            cur += timedelta(days=1)
            continue

        # 3) Excluir días sin horario del profesor
        if not await professor_works_day(session, teacher_id, cur):
            cur += timedelta(days=1)
            continue

        count += 1
        cur += timedelta(days=1)

    return count


# ---------------------------------------------------------
# AUSENCIAS compactadas
# ---------------------------------------------------------
def _build_rows(absences: List[Absence], name_by_id: Dict[int, str]) -> List[List[str]]:
    # AGRUPA POR PROFESOR Y CATEGORÍA
    by_key: Dict[Tuple[int, str], Dict[date, int]] = {}

    for a in absences:
        if not a.category or a.category == "Z":
            continue
        key = (a.teacher_id, a.category)
        by_key.setdefault(key, {})
        by_key[key][a.date] = a.hours_mask or 0

    rows: List[List[str]] = []

    # RECORRER CADA PROFESOR-CATEGORÍA
    for (tid, cat), days in sorted(
        by_key.items(),
        key=lambda x: (name_by_id.get(x[0][0], ""), x[0][1])
    ):
        dates = sorted(days.keys())
        if not dates:
            continue

        # DIVIDIR EN SEGMENTOS CONSECUTIVOS
        segments = []
        seg = [dates[0]]
        for d in dates[1:]:
            if (d - seg[-1]).days == 1:
                seg.append(d)
            else:
                segments.append(seg)
                seg = [d]
        segments.append(seg)

        # UNA FILA POR CADA SEGMENTO (AQUÍ ESTABA EL FALLO)
        for seg in segments:
            masks = [days[d] for d in seg]
            first = masks[0]

            if all(m == first for m in masks):
                hours_list = mask_to_hour_list(first)
                hours_text = "Todas" if len(hours_list) == 6 else ",".join(str(h) for h in hours_list)
            else:
                hours_text = "varias"

            fecha_txt, n_days_seg = _format_date_span(seg)

            rows.append([
                name_by_id.get(tid, f"ID {tid}"),
                fecha_txt,
                hours_text,
                cat,
                str(n_days_seg),
            ])

    return rows

# ---------------------------------------------------------
# PARTE MENSUAL (principal)
# ---------------------------------------------------------
async def build_monthly_report_pdf(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    path_out: str,
):
    # -----------------------------
    # Cargar calendario escolar
    # -----------------------------
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc()).limit(1)
        )
    ).scalar_one_or_none()

    # -----------------------------
    # AUSENCIAS
    # -----------------------------
    res_abs = await session.execute(
        select(Absence).where(
            and_(Absence.date >= date_from, Absence.date <= date_to)
        )
    )
    absences = list(res_abs.scalars().all())

    # -----------------------------
    # BAJAS
    # -----------------------------
    res_lv = await session.execute(
        select(Leave).where(
            or_(
                and_(Leave.start_date >= date_from, Leave.start_date <= date_to),
                and_(Leave.end_date != None, Leave.end_date >= date_from, Leave.end_date <= date_to),
                and_(Leave.start_date <= date_from,
                     or_(Leave.end_date == None, Leave.end_date >= date_to))
            )
        )
    )
    leaves = list(res_lv.scalars().all())

    # ¿Hay sin catalogar?
    has_uncategorized = any(a.category is None for a in absences)
    for lv in leaves:
        if lv.cause and lv.cause.lower().strip() == "excedencia":
            continue
        if lv.category is None:
            has_uncategorized = True

    # -----------------------------
    # NOMBRES
    # -----------------------------
    teacher_ids = {a.teacher_id for a in absences} | {lv.teacher_id for lv in leaves}

    name_by_id = {}
    if teacher_ids:
        qnames = await session.execute(
            select(Teacher.id, Teacher.name).where(Teacher.id.in_(teacher_ids))
        )
        name_by_id = {tid: nm for tid, nm in qnames.all()}

    # -----------------------------
    # FILAS AUSENCIAS
    # -----------------------------
    # Separar ausencias catalogadas de no catalogadas
    catalogadas = [a for a in absences if a.category and a.category != "Z"]
    sin_catalogar = [a for a in absences if not a.category or a.category == "Z"]
    
    has_uncategorized = len(sin_catalogar) > 0
    
    rows = _build_rows(catalogadas, name_by_id)

    # -----------------------------
    # FILAS BAJAS (con calendario)
    # -----------------------------
    for lv in leaves:
        if lv.cause and lv.cause.lower().strip() == "excedencia":
            continue

        name = name_by_id.get(lv.teacher_id, f"ID {lv.teacher_id}")
        cat = lv.category

        # Recorte a fechas del mes
        start = max(lv.start_date, date_from)
        end = lv.end_date or date_to
        end = min(end, date_to)

        # Días lectivos reales
        if cal:
            n_days = await working_school_days(session, cal, lv.teacher_id, start, end)
        else:
            # fallback: solo días L-V
            n_days = sum(
                1 for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5
            )

        # Texto fecha
        if lv.end_date:
            fecha_txt = f"del {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
        else:
            fecha_txt = f"desde {start.strftime('%d/%m/%Y')}"

        rows.append([
            name,
            fecha_txt,
            "Todas",
            cat,
            str(n_days),
        ])

    # -----------------------------
    # ORDEN FINAL
    # -----------------------------
    rows = sorted(rows, key=lambda r: (r[0].lower(), r[1]))

    # -----------------------------
    # PDF
    # -----------------------------
    doc = SimpleDocTemplate(
        path_out,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()

    style_center_small = ParagraphStyle(
        name="CenterSmall",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
    )

    style_title = ParagraphStyle(
        name="Title",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=16,
        leading=20,
    )

    elements = []

    # Logo
    if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
        img = Image(settings.LOGO_PATH, width=22 * mm, height=22 * mm)
        img.hAlign = "CENTER"
        elements.append(img)
        elements.append(Spacer(1, 4))

    # Centro
    if settings.INSTITUTION_NAME:
        elements.append(Paragraph(settings.INSTITUTION_NAME, style_center_small))

    # Título
    ultimo = monthrange(date_from.year, date_from.month)[1]
    mes_completo = (
        date_from.day == 1 and
        date_to.day == ultimo and
        date_from.month == date_to.month
    )

    if mes_completo:
        meses = {
            1:"ENERO",2:"FEBRERO",3:"MARZO",4:"ABRIL",
            5:"MAYO",6:"JUNIO",7:"JULIO",8:"AGOSTO",
            9:"SEPTIEMBRE",10:"OCTUBRE",11:"NOVIEMBRE",12:"DICIEMBRE"
        }
        titulo = f"Parte mensual de ausencias {meses[date_from.month]} de {date_from.year}"
    else:
        titulo = f"Parte mensual de ausencias ({date_from.strftime('%d/%m/%Y')} – {date_to.strftime('%d/%m/%Y')})"

    elements.append(Paragraph(titulo, style_title))
    elements.append(Spacer(1, 10))

    # Aviso
    if has_uncategorized:
        aviso = '<font color="red"><b>AVISO:</b> Existen AUSENCIAS o BAJAS sin catalogar en el rango.</font>'
        elements.append(Paragraph(aviso, styles["Normal"]))

    # Tabla
    data = [
        ["NOMBRE", "FECHA", "HORAS", "CAUSA", "DÍAS"]
    ] + rows

    table = Table(
        data,
        colWidths=[
            A4[0] * 0.30,
            A4[0] * 0.22,
            A4[0] * 0.15,
            A4[0] * 0.18,
            A4[0] * 0.10,
        ]
    )

    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    elements.append(table)
    doc.build(elements)

    return has_uncategorized, rows
