# services/pdf_monthly.py
from __future__ import annotations
from typing import List, Dict, Tuple
from datetime import date, timedelta
import os
from calendar import monthrange

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm, mm

from models import Absence, Leave, Teacher, TeacherStatus
from utils import mask_to_hour_list
from config import settings


# ---------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------
def _daterange(d0: date, d1: date) -> List[date]:
    cur = d0
    out = []
    while cur <= d1:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _format_date_span(dates: List[date]) -> Tuple[str, int]:
    dates_sorted = sorted(dates)
    if not dates_sorted:
        return "", 0
    d0, d1 = dates_sorted[0], dates_sorted[-1]
    if d0 == d1:
        return d0.strftime("%d/%m/%Y"), 1
    return f"del {d0.strftime('%d/%m/%Y')} al {d1.strftime('%d/%m/%Y')}", len(dates_sorted)


# ---------------------------------------------------------
# AUSENCIAS (compactación por profesor + categoría)
# ---------------------------------------------------------
def _build_rows(absences: List[Absence], name_by_id: Dict[int, str]) -> List[List[str]]:
    by_tc: Dict[Tuple[int, str], Dict[date, int]] = {}

    for a in absences:
        if not a.category or a.category == "Z":
            continue
        key = (a.teacher_id, a.category)
        by_tc.setdefault(key, {})
        by_tc[key][a.date] = a.hours_mask or 0

    rows: List[List[str]] = []

    for (tid, cat), days in sorted(
        by_tc.items(),
        key=lambda x: (name_by_id.get(x[0][0], ""), x[0][1])
    ):
        dates = sorted(days.keys())
        if not dates:
            continue

        segments: List[List[date]] = []
        cur_seg = [dates[0]]

        for d in dates[1:]:
            if (d - cur_seg[-1]).days == 1:
                cur_seg.append(d)
            else:
                segments.append(cur_seg)
                cur_seg = [d]
        segments.append(cur_seg)

        for seg in segments:
            first_mask = days[seg[0]]
            same = all(days[d] == first_mask for d in seg)

            if same:
                hours_list = mask_to_hour_list(first_mask)
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
# SERVICIO PRINCIPAL
# ---------------------------------------------------------
async def build_monthly_report_pdf(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    path_out: str
) -> Tuple[bool, List[List[str]]]:

    # 1) AUSENCIAS
    res = await session.execute(
        select(Absence).where(and_(Absence.date >= date_from, Absence.date <= date_to))
    )
    absences = res.scalars().all()

    # 2) BAJAS SOLAPADAS CON EL RANGO
    res_leaves = await session.execute(
        select(Leave).where(
            or_(
                and_(Leave.start_date >= date_from, Leave.start_date <= date_to),
                and_(Leave.end_date != None,
                     Leave.end_date >= date_from,
                     Leave.end_date <= date_to),
                and_(Leave.start_date <= date_from,
                     or_(Leave.end_date == None, Leave.end_date >= date_to))
            )
        )
    )
    leaves = res_leaves.scalars().all()

    # 3) DETECTAR AUSENCIAS O BAJAS SIN CATEGORIZAR
    has_uncategorized = any(
        a.category is None for a in absences
    )

    for lv in leaves:
        if lv.leave_type == TeacherStatus.excedencia:
            continue
        if lv.category is None:
            has_uncategorized = True

    # 4) NOMBRES DE PROFESORES
    tids = list({a.teacher_id for a in absences})
    tids += [lv.teacher_id for lv in leaves]
    tids = list(set(tids))

    if tids:
        qn = await session.execute(
            select(Teacher.id, Teacher.name).where(Teacher.id.in_(tids))
        )
        name_by_id = {tid: tname for tid, tname in qn.all()}
    else:
        name_by_id = {}

    # 5) FILAS DE AUSENCIAS
    rows = _build_rows(
        [a for a in absences if a.category and a.category != "Z"],
        name_by_id,
    )

    # 6) FILAS POR BAJA
    for lv in leaves:
        if lv.leave_type == TeacherStatus.excedencia:
            continue

        cat = lv.category

        if lv.end_date:
            fecha_text = (
                f"del {lv.start_date.strftime('%d/%m/%Y')} "
                f"al {lv.end_date.strftime('%d/%m/%Y')}"
            )
            n_days = (lv.end_date - lv.start_date).days + 1
        else:
            fecha_text = f"desde {lv.start_date.strftime('%d/%m/%Y')}"
            n_days = (date_to - lv.start_date).days + 1

        rows.append([
            name_by_id.get(lv.teacher_id, f"ID {lv.teacher_id}"),
            fecha_text,
            "Todas",
            cat,
            str(n_days),
        ])

    # ---------------------------------------------------------
    # 7) GENERAR PDF con LOGO + CABECERO INTELIGENTE
    # ---------------------------------------------------------
    doc = SimpleDocTemplate(
        path_out,
        pagesize=A4,
        leftMargin=18*mm,
        rightMargin=18*mm,
        topMargin=18*mm,
        bottomMargin=18*mm,
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

    # --------- LOGO ---------
    logo_path = settings.LOGO_PATH
    if logo_path and os.path.exists(logo_path):
        img = Image(logo_path, width=22*mm, height=22*mm)
        img.hAlign = "CENTER"
        elements.append(img)
        elements.append(Spacer(1, 4))

    # --------- INSTITUTION NAME ---------
    if settings.INSTITUTION_NAME:
        elements.append(Paragraph(settings.INSTITUTION_NAME, style_center_small))

    # --------- TÍTULO INTELIGENTE ---------
    # Si el rango cubre un MES COMPLETO → usar “MARZO de 2026”
    ultimo_dia_mes = monthrange(date_from.year, date_from.month)[1]

    es_mes_completo = (
        date_from.day == 1 and
        date_to.day == ultimo_dia_mes and
        date_from.month == date_to.month and
        date_from.year == date_to.year
    )

    if es_mes_completo:
        nombre_mes = date_from.strftime("%B").upper()   # Ej: MARCH → MARCH → lo convertiremos a español si quieres
        # En español:
        meses = {
            "JANUARY": "ENERO", "FEBRUARY": "FEBRERO", "MARCH": "MARZO",
            "APRIL": "ABRIL", "MAY": "MAYO", "JUNE": "JUNIO",
            "JULY": "JULIO", "AUGUST": "AGOSTO", "SEPTEMBER": "SEPTIEMBRE",
            "OCTOBER": "OCTUBRE", "NOVEMBER": "NOVIEMBRE", "DECEMBER": "DICIEMBRE"
        }
        nombre_mes = meses[nombre_mes]

        titulo = f"Parte mensual de ausencias {nombre_mes} de {date_from.year}"
    else:
        titulo = (
            f"Parte mensual de ausencias "
            f"({date_from.strftime('%d/%m/%Y')} – {date_to.strftime('%d/%m/%Y')})"
        )

    elements.append(Paragraph(titulo, style_title))

    # --------- AVISO ---------
    if has_uncategorized:
        txt = (
            '<font color="red"><b>AVISO:</b> '
            'Existen AUSENCIAS o BAJAS sin catalogar en el rango seleccionado.'
            '</font>'
        )
        elements.append(Paragraph(txt, styles["Normal"]))

    elements.append(Spacer(1, 10))

    # --------- TABLA ---------
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
