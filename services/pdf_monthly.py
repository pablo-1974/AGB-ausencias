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

from models import Absence, Leave, Teacher
from utils import mask_to_hour_list
from config import settings


# ---------------------------------------------------------
# AUX
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
# AUSENCIAS
# ---------------------------------------------------------
def _build_rows(absences: List[Absence], name_by_id: Dict[int, str]) -> List[List[str]]:
    by_key: Dict[Tuple[int, str], Dict[date, int]] = {}

    for a in absences:
        if not a.category or a.category == "Z":
            continue
        key = (a.teacher_id, a.category)
        by_key.setdefault(key, {})
        by_key[key][a.date] = a.hours_mask or 0

    rows: List[List[str]] = []

    for (tid, cat), days in sorted(by_key.items(), key=lambda x: (name_by_id.get(x[0][0], ""), x[0][1])):
        dates = sorted(days.keys())
        if not dates:
            continue

        segments = []
        seg = [dates[0]]
        for d in dates[1:]:
            if (d - seg[-1]).days == 1:
                seg.append(d)
            else:
                segments.append(seg)
                seg = [d]
        segments.append(seg)

        for seg in segments:
            masks = [days[d] for d in seg]
            first = masks[0]
            same = all(m == first for m in masks)
            if same:
                hours_list = mask_to_hour_list(first)
                hours_text = "Todas" if len(hours_list) == 6 else ",".join(str(h) for h in hours_list)
            else:
                hours_text = "varias"

            fecha_text, n_days = _format_date_span(seg)

            rows.append([
                name_by_id.get(tid, f"ID {tid}"),
                fecha_text,
                hours_text,
                cat,
                str(n_days),
            ])

    return rows


# ---------------------------------------------------------
# MAIN SERVICE
# ---------------------------------------------------------
async def build_monthly_report_pdf(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    path_out: str
):
    # 1) AUSENCIAS
    res_abs = await session.execute(
        select(Absence).where(
            and_(
                Absence.date >= date_from,
                Absence.date <= date_to
            )
        )
    )
    absences = list(res_abs.scalars().all())

    # 2) BAJAS solapadas
    res_lv = await session.execute(
        select(Leave).where(
            or_(
                and_(Leave.start_date >= date_from, Leave.start_date <= date_to),
                and_(Leave.end_date != None, Leave.end_date >= date_from, Leave.end_date <= date_to),
                and_(Leave.start_date <= date_from, or_(Leave.end_date == None, Leave.end_date >= date_to))
            )
        )
    )
    leaves = list(res_lv.scalars().all())

    # 3) SIN CATEGORIZAR
    has_uncategorized = any(a.category is None for a in absences)

    for lv in leaves:
        if lv.cause and lv.cause.lower().strip() == "excedencia":
            continue
        if lv.category is None:
            has_uncategorized = True

    # 4) NOMBRES
    teacher_ids = {a.teacher_id for a in absences} | {lv.teacher_id for lv in leaves}

    name_by_id = {}
    if teacher_ids:
        res_names = await session.execute(
            select(Teacher.id, Teacher.name).where(Teacher.id.in_(teacher_ids))
        )
        name_by_id = {tid: name for tid, name in res_names.all()}

    # 5) FILAS AUSENCIAS
    rows = _build_rows(
        [a for a in absences if a.category and a.category != "Z"],
        name_by_id,
    )

    # 6) FILAS BAJAS
    for lv in leaves:
        if lv.cause and lv.cause.lower().strip() == "excedencia":
            continue

        name = name_by_id.get(lv.teacher_id, f"ID {lv.teacher_id}")
        cat = lv.category

        if lv.end_date:
            fecha = f"del {lv.start_date.strftime('%d/%m/%Y')} al {lv.end_date.strftime('%d/%m/%Y')}"
            n_days = (lv.end_date - lv.start_date).days + 1
        else:
            fecha = f"desde {lv.start_date.strftime('%d/%m/%Y')}"
            n_days = (date_to - lv.start_date).days + 1

        rows.append([
            name,
            fecha,
            "Todas",
            cat,
            str(n_days),
        ])

    # 7) PDF
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

    # LOGO
    if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
        img = Image(settings.LOGO_PATH, width=22 * mm, height=22 * mm)
        img.hAlign = "CENTER"
        elements.append(img)
        elements.append(Spacer(1, 4))

    # NOMBRE CENTRO
    if settings.INSTITUTION_NAME:
        elements.append(Paragraph(settings.INSTITUTION_NAME, style_center_small))

    # TITULO INTELIGENTE
    ultimo = monthrange(date_from.year, date_from.month)[1]
    mes_completo = (
        date_from.day == 1 and
        date_to.day == ultimo and
        date_from.month == date_to.month and
        date_from.year == date_to.year
    )

    if mes_completo:
        meses = {
            1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
            5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
            9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
        }
        mes = meses[date_from.month]
        titulo = f"Parte mensual de ausencias {mes} de {date_from.year}"
    else:
        titulo = f"Parte mensual de ausencias ({date_from.strftime('%d/%m/%Y')} – {date_to.strftime('%d/%m/%Y')})"

    elements.append(Paragraph(titulo, style_title))

    # AVISO
    if has_uncategorized:
        aviso = (
            '<font color="red"><b>AVISO:</b> '
            'Existen AUSENCIAS o BAJAS sin catalogar en el rango seleccionado.'
            '</font>'
        )
        elements.append(Paragraph(aviso, styles["Normal"]))

    elements.append(Spacer(1, 10))

    # TABLA
    head = ["NOMBRE", "FECHA", "HORAS", "CAUSA", "DÍAS"]
    data = [head] + rows

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
