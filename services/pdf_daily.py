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


# Mapeos
HOUR_ROWS = [("1ª", 0), ("2ª", 1), ("3ª", 2), ("RECREO", 3), ("4ª", 4), ("5ª", 5), ("6ª", 6)]
DAYS = {0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"}


def _is_absent(mask: int, hour_idx: int) -> bool:
    return (mask & (1 << hour_idx)) != 0


# ======================================================================
#  AUSENTES DEL DÍA
# ======================================================================
async def _teachers_absent_that_day(session: AsyncSession, the_date: date) -> Tuple[Set[int], Dict[int, int]]:

    # Ausencias manuales
    q_abs = select(Absence).where(Absence.date == the_date)
    absences = (await session.execute(q_abs)).scalars().all()

    hours_by_teacher: Dict[int, int] = {}
    absent_ids: Set[int] = set()

    for a in absences:
        absent_ids.add(a.teacher_id)
        hours_by_teacher[a.teacher_id] = hours_by_teacher.get(a.teacher_id, 0) | (a.hours_mask or 0)

    # Bajas sin sustituto → ausente todo el día
    q_leave = select(Leave).where(
        and_(
            Leave.start_date <= the_date,
            or_(Leave.end_date == None, Leave.end_date >= the_date),
            or_(Leave.substitute_teacher_id == None, Leave.substitute_teacher_id == 0),
        )
    )
    leaves = (await session.execute(q_leave)).scalars().all()

    FULL_MASK = make_mask_all()  # 127 real

    for lv in leaves:
        absent_ids.add(lv.teacher_id)
        hours_by_teacher[lv.teacher_id] = hours_by_teacher.get(lv.teacher_id, 0) | FULL_MASK

    return absent_ids, hours_by_teacher



# ======================================================================
#  PDF
# ======================================================================
async def build_daily_report_pdf(
    session: AsyncSession,
    the_date: date,
    path_out: str,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3
):

    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    # Nombres
    if absent_ids:
        q_teachers = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: name for tid, name in (await session.execute(q_teachers)).all()}
    else:
        name_by_id = {}

    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    data = [head]

    obs_lines: List[str] = []
    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py)

    # ----------------------------------------------------------------------
    #  CONSTRUCCIÓN DE FILAS
    # ----------------------------------------------------------------------
    for label, hour_idx in HOUR_ROWS:

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        # ----------------------------
        # AUSENTES
        # ----------------------------
        for tid in sorted(absent_ids):

            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            prof_name = name_by_id.get(tid)
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)

            if not slot:
                continue  # NO clase ni guardia

            # CLASE
            if slot.type == ScheduleType.CLASS:
                row_prof.append(prof_name)
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")

            # GUARDIA
            else:
                gtext = (slot.guard_type or "").upper()

                if "RECREO" in gtext:
                    obs_lines.append(f"{prof_name}: {gtext}")
                    row_prof.append(prof_name)
                    row_grp.append("")
                    row_room.append("")
                    row_subj.append("")
                else:
                    row_prof.append(prof_name)
                    row_grp.append("guardia")
                    row_room.append("guardia")
                    row_subj.append("guardia")

        # ----------------------------
        # PROFESORES EN GUARDIA
        # ----------------------------
        guard_ids = await list_teachers_on_guard(session, weekday_py, hour_idx, absent_ids)

        guard_aliases = []

        for tid in guard_ids:

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)

            if not slot or slot.type != ScheduleType.GUARD:
                continue

            gtext = (slot.guard_type or "").upper()

            # NO mostrar guardia de recreo
            if "RECREO" in gtext:
                continue

            t = await session.get(Teacher, tid)
            guard_aliases.append(t.alias or t.name)

        def crush(xs: List[str]) -> str:
            return "\n".join([str(s) for s in xs if s and str(s).strip()])

        data.append([
            label,
            crush(row_prof),
            crush(row_grp),
            crush(row_room),
            crush(row_subj),
            "",
            crush(guard_aliases)
        ])

    # ----------------------------------------------------------------------
    # PDF MAQUETACIÓN
    # ----------------------------------------------------------------------
    doc = SimpleDocTemplate(
        path_out,
        pagesize=A4,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
    )

    styles = getSampleStyleSheet()
    elements: List = []

    elements.append(Paragraph(
        f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
        styles["Title"]
    ))
    elements.append(Spacer(1, 6))

    # Observaciones
    obs_text = ""
    if obs_lines or observaciones_usuario:
        parts = []
        if obs_lines:
            parts.append("; ".join(obs_lines))
        if observaciones_usuario:
            parts.append(observaciones_usuario)
        obs_text = "; ".join(parts)

    elements.append(Paragraph(f"<b>Observaciones:</b> {obs_text}", styles["Normal"]))
    elements.append(Spacer(1, 8))

    # Columnas
    total_width = A4[0] - doc.leftMargin - doc.rightMargin
    col_widths = [
        1.2 * cm,
        total_width * 0.24,
        total_width * 0.13,
        total_width * 0.12,
        total_width * 0.16,
        total_width * 0.12,
        total_width * 0.21,
    ]

    row_heights = [16] + [58] * 7

    table = Table(data, colWidths=col_widths, rowHeights=row_heights)

    ts = TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),

        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),

        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 4), (-1, 4), colors.lightgrey),

        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("FONTSIZE", (6, 1), (6, -1), 7),  # columna GUARDIA

        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])

    table.setStyle(ts)

    elements.append(table)
    doc.build(elements)



# ======================================================================
# HTML PREVIEW
# ======================================================================
async def build_daily_report_data(
    session: AsyncSession,
    the_date: date,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3
):
    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    if absent_ids:
        q_teachers = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: name for tid, name in (await session.execute(q_teachers)).all()}
    else:
        name_by_id = {}

    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    rows = []

    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py)

    obs_lines: List[str] = []

    for label, hour_idx in HOUR_ROWS:

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        # AUSENTES
        for tid in sorted(absent_ids):

            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            prof_name = name_by_id.get(tid)
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)

            if not slot:
                continue

            if slot.type == ScheduleType.CLASS:
                row_prof.append(prof_name)
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")

            else:
                gtext = (slot.guard_type or "").upper()

                if "RECREO" in gtext:
                    obs_lines.append(f"{prof_name}: {gtext.lower()}")
                    row_prof.append(prof_name)
                    row_grp.append("")
                    row_room.append("")
                    row_subj.append("")
                else:
                    row_prof.append(prof_name)
                    row_grp.append("guardia")
                    row_room.append("guardia")
                    row_subj.append("guardia")

        # GUARDIAS (NO ausentes)
        guard_ids = await list_teachers_on_guard(session, weekday_py, hour_idx, absent_ids)

        guard_aliases = []

        for tid in guard_ids:

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)

            if not slot or slot.type != ScheduleType.GUARD:
                continue

            gtext = (slot.guard_type or "").upper()

            if "RECREO" in gtext:
                continue

            t = await session.get(Teacher, tid)
            guard_aliases.append(t.alias or t.name)

        def crush(xs: List[str]):
            return "\n".join([x for x in xs if x.strip()])

        rows.append([
            label,
            crush(row_prof),
            crush(row_grp),
            crush(row_room),
            crush(row_subj),
            "",
            crush(guard_aliases),
        ])

    # Observaciones
    obs_text = ""
    if obs_lines or observaciones_usuario:
        parts = []
        if obs_lines:
            parts.append("; ".join(obs_lines))
        if observaciones_usuario:
            parts.append(observaciones_usuario)
        obs_text = "; ".join(parts)

    return {
        "title": f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
        "weekday_name": weekday_name,
        "date_str": the_date.isoformat(),
        "head": head,
        "rows": rows,
        "obs_text": obs_text,
    }


