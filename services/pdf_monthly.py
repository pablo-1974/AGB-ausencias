# services/pdf_monthly.py — VERSIÓN FINAL (mensual por días, correcta)

from __future__ import annotations
from typing import List, Dict, Tuple
from datetime import date, timedelta
from calendar import monthrange
import os
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import mm

from models import Absence, Leave, Teacher, ScheduleSlot, SchoolCalendar
from utils import mask_to_hour_list, normalize_name
from config import settings


# ----------------------------------------------
# AUX ― Filtros de fechas (NO SE MODIFICA)
# ----------------------------------------------
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


async def professor_works_day(session: AsyncSession, teacher_id: int, day: date) -> bool:
    weekday = day.weekday()
    if weekday >= 5:
        return False

    res = await session.execute(
        select(ScheduleSlot.id).where(
            ScheduleSlot.teacher_id == teacher_id,
            ScheduleSlot.day_index == weekday
        )
    )
    return res.first() is not None


def is_holiday(day: date, cal: SchoolCalendar) -> bool:
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


# =====================================================================
# PARTE MENSUAL — LÓGICA CORRECTA (RECORRIDO DIARIO)
# =====================================================================
async def build_monthly_report_pdf(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    path_out: str,
):
    # 1) Calendario (NO SE TOCA)
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc()).limit(1)
        )
    ).scalar_one_or_none()

    # -----------------------------------------------------
    # 2) ACUMULADOR MENSUAL POR (teacher_id, causa)
    # -----------------------------------------------------
    acc: Dict[Tuple[int, str], List[date]] = defaultdict(list)

    cur = date_from
    while cur <= date_to:
        # solo días lectivos
        if cur.weekday() >= 5 or is_holiday(cur, cal):
            cur += timedelta(days=1)
            continue

        # -------------------------
        # AUSENCIAS PUNTUALES
        # -------------------------
        res_abs = await session.execute(
            select(Absence).where(Absence.date == cur)
        )
        for a in res_abs.scalars():
            if not a.category or a.category == "Z":
                continue
            acc[(a.teacher_id, a.category)].append(cur)

        # -------------------------
        # BAJAS ACTIVAS (NO EXCEDENCIA)
        # -------------------------
        res_lv = await session.execute(
            select(Leave).where(
                and_(
                    Leave.start_date <= cur,
                    or_(Leave.end_date == None, Leave.end_date >= cur),
                )
            )
        )
        for lv in res_lv.scalars():
            # excluir excedencias
            if lv.cause and "excedencia" in lv.cause.lower():
                continue
        
            # ✅ ESTE ES EL FILTRO QUE FALTA
            if lv.substitute_teacher_id is not None:
                continue
        
            # NO contar días anteriores al inicio real de la baja
            if cur < lv.start_date:
                continue
        
            cat = lv.category or "Baja médica"
            acc[(lv.teacher_id, cat)].append(cur)

        cur += timedelta(days=1)

    # -----------------------------------------------------
    # 3) NOMBRES
    # -----------------------------------------------------
    teacher_ids = {tid for (tid, _) in acc.keys()}
    if teacher_ids:
        q = await session.execute(
            select(Teacher.id, Teacher.name).where(Teacher.id.in_(teacher_ids))
        )
        name_by_id = {tid: nm for tid, nm in q.all()}
    else:
        name_by_id = {}

    # -----------------------------------------------------
    # 4) CONSTRUIR FILAS (MÚLTIPLES POR PROFESOR)
    # -----------------------------------------------------
    rows: List[List[str]] = []

    for (tid, cat), days in sorted(
        acc.items(),
        key=lambda x: (normalize_name(name_by_id.get(x[0][0], "")), x[0][1])
    ):
        days = sorted(set(days))
        if not days:
            continue

        # agrupar en tramos consecutivos
        segment = [days[0]]
        segments = []
        
        for d in days[1:]:
            next_day = segment[-1] + timedelta(days=1)
            while next_day.weekday() >= 5 or is_holiday(next_day, cal):
                next_day += timedelta(days=1)
        
            if d == next_day:
                segment.append(d)
            else:
                segments.append(segment)
                segment = [d]
        
        segments.append(segment)

        for seg in segments:
            fecha_text, _ = _format_date_span(seg)
            n_days = len(seg)
            rows.append([
                name_by_id.get(tid, f"ID {tid}"),
                fecha_text,
                "Todas",
                cat,
                str(n_days),
            ])

    # =================================================================
    # TODO LO DEMÁS — MAQUETACIÓN PDF (NO SE TOCA)
    # =================================================================
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
    if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
        img = Image(settings.LOGO_PATH, width=22 * mm, height=22 * mm)
        img.hAlign = "CENTER"
        elements.append(img)
        elements.append(Spacer(1, 4))

    if settings.INSTITUTION_NAME:
        elements.append(Paragraph(settings.INSTITUTION_NAME, style_center_small))

    ultimo = monthrange(date_from.year, date_from.month)[1]
    mes_completo = (
        date_from.day == 1 and
        date_to.day == ultimo and
        date_from.month == date_to.month
    )

    if mes_completo:
        meses = {
            1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
            5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
            9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
        }
        titulo = f"Parte mensual de ausencias {meses[date_from.month]} de {date_from.year}"
    else:
        titulo = (
            f"Parte mensual de ausencias "
            f"({date_from.strftime('%d/%m/%Y')} – {date_to.strftime('%d/%m/%Y')})"
        )

    elements.append(Paragraph(titulo, style_title))
    elements.append(Spacer(1, 10))

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
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("FONTSIZE", (0,1), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    elements.append(table)
    doc.build(elements)

    return False, rows
