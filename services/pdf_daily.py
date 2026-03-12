# services/pdf_daily.py
from __future__ import annotations
from typing import List, Tuple, Set, Dict
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Absence, Leave, Teacher, ScheduleType
from .schedule import get_teacher_slot, list_teachers_on_guard


# Mapeos según tu app
HOUR_ROWS = [("1ª", 0), ("2ª", 1), ("3ª", 2), ("RECREO", 3), ("4ª", 4), ("5ª", 5), ("6ª", 6)]
DAYS = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}


def _bit_on(mask: int, hour_one_based: int) -> bool:
    return (mask & (1 << (hour_one_based - 1))) != 0


async def _teachers_absent_that_day(session: AsyncSession, the_date: date) -> Tuple[Set[int], Dict[int, int]]:
    """
    Devuelve:
    - set de teacher_id ausentes (por ausencia manual y/o baja sin sustituto)
    - dict teacher_id -> bitmask de horas ausentes (manuales). En bajas sin sustituto, activamos todo el día.
    """
    # Ausencias manuales
    q_abs = select(Absence).where(Absence.date == the_date)
    absences = (await session.execute(q_abs)).scalars().all()

    hours_by_teacher: Dict[int, int] = {}
    absent_ids: Set[int] = set()

    for a in absences:
        absent_ids.add(a.teacher_id)
        hours_by_teacher[a.teacher_id] = hours_by_teacher.get(a.teacher_id, 0) | (a.hours_mask or 0)

    # Bajas sin sustituto → ausente todo el día (1..6)
    q_leave = select(Leave).where(
        and_(Leave.start_date <= the_date, or_(Leave.end_date == None, Leave.end_date >= the_date),
             or_(Leave.substitute_teacher_id == None, Leave.substitute_teacher_id == 0))
    )
    leaves = (await session.execute(q_leave)).scalars().all()
    FULL_MASK = (1 << 6) - 1
    for lv in leaves:
        absent_ids.add(lv.teacher_id)
        hours_by_teacher[lv.teacher_id] = hours_by_teacher.get(lv.teacher_id, 0) | FULL_MASK

    return absent_ids, hours_by_teacher


async def build_daily_report_pdf(
    session: AsyncSession,
    the_date: date,
    path_out: str,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3,  # 0..6
) -> None:
    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    # Nombres de ausentes
    if absent_ids:
        q_teachers = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: tname for tid, tname in (await session.execute(q_teachers)).all()}
    else:
        name_by_id = {}

    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    data = [head]
    obs_lines: List[str] = []

    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py, "")

    for label, hour_idx in HOUR_ROWS:
        row_prof, row_grp, row_room, row_subj, row_guard = [], [], [], [], []

        # Ausentes en esta hora
        for tid in sorted(absent_ids):
            mask = hours_by_teacher.get(tid, 0)
            # En manual, el RECREO suele no marcarse; en baja sin sustituto asumimos todo el día
            is_abs_now = (_bit_on(mask, hour_idx + 1) if hour_idx != recreo_index else True)
            if not is_abs_now:
                continue

            tname = name_by_id.get(tid, f"ID {tid}")
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)

            if not slot:
                # No hay info de clase/guardia
                row_prof.append(tname); row_grp.append(""); row_room.append(""); row_subj.append("")
            else:
                if slot.type == ScheduleType.CLASS:
                    row_prof.append(tname)
                    row_grp.append(slot.group or ""); row_room.append(slot.room or ""); row_subj.append(slot.subject or "")
                else:
                    # Guardia
                    gtext = (slot.guard_type or "").upper()
                    if "RECREO" in gtext:
                        # Guardias de recreo → a observaciones
                        obs_lines.append(f"{tname}: {gtext.replace('G ', '').lower()}")
                        row_prof.append(tname); row_grp.append(""); row_room.append(""); row_subj.append("")
                    else:
                        # Guardia de aula
                        row_prof.append(tname); row_grp.append("guardia"); row_room.append("guardia"); row_subj.append("guardia")

        # Profes en guardia (no ausentes)
        guard_names = await list_teachers_on_guard(session, weekday_py, hour_idx, absent_ids)
        row_guard = guard_names

        def crush(xs: List[str]) -> str:
            return "\n".join([s for s in xs if s and s.strip()])

        data.append([
            label,
            crush(row_prof),
            crush(row_grp),
            crush(row_room),
            crush(row_subj),
            "",  # firmas
            crush(row_guard),
        ])

    # ---------- PDF maquetación ----------
    doc = SimpleDocTemplate(path_out, pagesize=A4,
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.0 * cm)
    styles = getSampleStyleSheet()
    elements: List = []

    title = f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})"
    elements.append(Paragraph(title, styles["Title"]))
    elements.append(Spacer(1, 6))

    obs_text = ""
    if obs_lines or observaciones_usuario:
        appended = []
        if obs_lines:
            appended.append("; ".join(obs_lines))
        if observaciones_usuario:
            appended.append(observaciones_usuario.strip())
        obs_text = "; ".join(appended)

    elements.append(Paragraph(f"<b>Observaciones:</b> {obs_text}", styles["Normal"]))
    elements.append(Spacer(1, 8))

    # Columnas con ancho fijo para ocupar toda la hoja
    total_width = A4[0] - doc.leftMargin - doc.rightMargin
    col_widths = [
        1.2 * cm,           # HORA
        total_width * 0.24, # PROFESOR
        total_width * 0.13, # GRUPO
        total_width * 0.12, # AULA
        total_width * 0.16, # ASIGN.
        total_width * 0.12, # FIRMAS
        total_width * 0.21, # GUARDIA
    ]
    header_h = 16
    row_h = 58
    row_heights = [header_h] + [row_h] * 7

    table = Table(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)
    ts = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        # Líneas horizontales de separación de franjas
        ("LINEBELOW", (0, 1), (-1, 1), 1, colors.black),
        ("LINEBELOW", (0, 2), (-1, 2), 0.8, colors.black),
        ("LINEBELOW", (0, 3), (-1, 3), 0.8, colors.black),
        ("LINEBELOW", (0, 4), (-1, 4), 0.8, colors.black),
        ("LINEBELOW", (0, 5), (-1, 5), 0.8, colors.black),
        ("LINEBELOW", (0, 6), (-1, 6), 0.8, colors.black),
        ("LINEBELOW", (0, 7), (-1, 7), 0.8, colors.black),
        ("LINEBELOW", (0, 8), (-1, 8), 1, colors.black),

        # RECREO en gris (cabecera + 3 filas => índice 4)
        ("BACKGROUND", (0, 4), (-1, 4), colors.lightgrey),

        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])
    table.setStyle(ts)

    elements.append(table)
    doc.build(elements)

# --- añade al final de services/pdf_daily.py (o cerca de build_daily_report_pdf) ---

async def build_daily_report_data(
    session: AsyncSession,
    the_date: date,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3,
):
    """
    Devuelve una estructura de datos para pintar el parte en HTML:
    {
      "title": "...",
      "weekday_name": "...",
      "date_str": "YYYY-MM-DD",
      "head": [ ... ],
      "rows": [ [col1..col7], ... ],
      "obs_text": "…"
    }
    Reutiliza la misma lógica de _teachers_absent_that_day y de construcción de filas.
    """
    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    # Nombres
    if absent_ids:
        q_teachers = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: tname for tid, tname in (await session.execute(q_teachers)).all()}
    else:
        name_by_id = {}

    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    data_rows = []

    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py, "")

    obs_lines: List[str] = []

    for label, hour_idx in HOUR_ROWS:
        row_prof, row_grp, row_room, row_subj, row_guard = [], [], [], [], []

        for tid in sorted(absent_ids):
            mask = hours_by_teacher.get(tid, 0)
            is_abs_now = (_bit_on(mask, hour_idx + 1) if hour_idx != recreo_index else True)
            if not is_abs_now:
                continue
            tname = name_by_id.get(tid, f"ID {tid}")
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot:
                row_prof.append(tname); row_grp.append(""); row_room.append(""); row_subj.append("")
            else:
                if slot.type == ScheduleType.CLASS:
                    row_prof.append(tname)
                    row_grp.append(slot.group or ""); row_room.append(slot.room or ""); row_subj.append(slot.subject or "")
                else:
                    gtext = (slot.guard_type or "").upper()
                    if "RECREO" in gtext:
                        obs_lines.append(f"{tname}: {gtext.replace('G ', '').lower()}")
                        row_prof.append(tname); row_grp.append(""); row_room.append(""); row_subj.append("")
                    else:
                        row_prof.append(tname); row_grp.append("guardia"); row_room.append("guardia"); row_subj.append("guardia")

        # Profes disponibles en guardia (no ausentes)
        guard_names = await list_teachers_on_guard(session, weekday_py, hour_idx, absent_ids)
        row_guard = guard_names

        def crush(xs: List[str]) -> str:
            return "\n".join([s for s in xs if s and s.strip()])

        data_rows.append([
            label,
            crush(row_prof),
            crush(row_grp),
            crush(row_room),
            crush(row_subj),
            "",
            crush(row_guard),
        ])

    # Observaciones combinadas
    obs_text = ""
    if obs_lines or observaciones_usuario:
        parts = []
        if obs_lines:
            parts.append("; ".join(obs_lines))
        if observaciones_usuario:
            parts.append(observaciones_usuario.strip())
        obs_text = "; ".join(parts)

    return {
        "title": f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
        "weekday_name": weekday_name,
        "date_str": the_date.isoformat(),
        "head": head,
        "rows": data_rows,
        "obs_text": obs_text,
    }
