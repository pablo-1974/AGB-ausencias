# services/pdf_monthly.py
from __future__ import annotations
from typing import List, Dict, Tuple
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

from models import Absence, Teacher
from utils import mask_to_hour_list


# ------------------------------
# Utilidades de agrupación
# ------------------------------
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


# ------------------------------
# Construcción de filas
# ------------------------------
def _build_rows(absences: List[Absence], name_by_id: Dict[int, str]) -> List[List[str]]:
    """
    Absences ya filtradas por rango y excluyendo Z.
    Agrupa por (teacher_id, category) y compacta días consecutivos.
    """
    # Agrupación por profe + causa
    by_tc: Dict[Tuple[int, str], Dict[date, int]] = {}

    for a in absences:
        if not a.category or a.category == "Z":
            continue
        key = (a.teacher_id, a.category)
        by_tc.setdefault(key, {})
        by_tc[key][a.date] = a.hours_mask or 0

    rows: List[List[str]] = []

    for (tid, cat), days in sorted(by_tc.items(), key=lambda x: (name_by_id.get(x[0][0], ""), x[0][1])):
        # Compactar tramos consecutivos
        dates = sorted(days.keys())
        if not dates:
            continue

        # Construimos segmentos consecutivos
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
            # Horas: si todas las fechas del segmento comparten el mismo patrón de horas, lo listamos;
            # si no, ponemos "varias".
            first_mask = days[seg[0]]
            same = all(days[d] == first_mask for d in seg)
            if same:
                hours_list = mask_to_hour_list(first_mask)
                hours_text = "Todas" if len(hours_list) == 6 else ",".join(str(h) for h in hours_list)
            else:
                hours_text = "varias"

            fecha_text, n_days = _format_date_span(seg)

            rows.append([
                name_by_id.get(tid, f"ID {tid}"),   # Nombre
                fecha_text,                         # Fecha
                hours_text,                         # Horas
                cat,                                # Causa
                str(n_days),                        # Días
            ])

    return rows


# ------------------------------
# Servicio principal
# ------------------------------
async def build_monthly_report_pdf(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    path_out: str
) -> Tuple[bool, List[List[str]]]:
    """
    Devuelve:
      - has_uncategorized: True si hay ausencias sin catalogar en el rango
      - rows: mismas filas que se pintan en PDF
    """
    # Traer ausencias del rango
    res = await session.execute(select(Absence).where(and_(Absence.date >= date_from, Absence.date <= date_to)))
    absences = res.scalars().all()

    # ¿Hay sin catalogar?
    has_uncategorized = any(a.category is None for a in absences)

    # Nombre de profesores
    tids = list({a.teacher_id for a in absences})
    if tids:
        qn = await session.execute(select(Teacher.id, Teacher.name).where(Teacher.id.in_(tids)))
        name_by_id = {tid: tname for tid, tname in qn.all()}
    else:
        name_by_id = {}

    # Excluir Z y construir filas
    rows = _build_rows([a for a in absences if a.category != "Z"], name_by_id)

    # ---------- PDF ----------
    doc = SimpleDocTemplate(path_out, pagesize=A4,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.0 * cm)
    styles = getSampleStyleSheet()
    elements = []

    title = f"Parte mensual de ausencias ({date_from.strftime('%d/%m/%Y')} – {date_to.strftime('%d/%m/%Y')})"
    elements.append(Paragraph(title, styles["Title"]))
    if has_uncategorized:
        elements.append(Paragraph('<font color="red"><b>AVISO:</b> Existen ausencias sin catalogar en el rango seleccionado.</font>', styles["Normal"]))
    elements.append(Spacer(1, 8))

    head = ["NOMBRE", "FECHA", "HORAS", "CAUSA", "DÍAS"]
    data = [head] + rows

    table = Table(data, colWidths=[A4[0]*0.30, A4[0]*0.22, A4[0]*0.15, A4[0]*0.18, A4[0]*0.10])
    ts = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])
    table.setStyle(ts)
    elements.append(table)

    doc.build(elements)
    return has_uncategorized, rows
