# services/pdf_daily.py
from __future__ import annotations
from typing import List, Tuple, Set, Dict
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Absence, Leave, Teacher, ScheduleType, TeacherStatus
from .schedule import get_teacher_slot, list_teachers_on_guard
from absences_router import make_mask_all


# 7 franjas reales
HOUR_ROWS = [
    ("1ª", 0),
    ("2ª", 1),
    ("3ª", 2),
    ("RECREO", 3),
    ("4ª", 4),
    ("5ª", 5),
    ("6ª", 6),
]

DAYS = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
    6: "domingo",
}


def _is_absent(mask: int, hour_idx: int) -> bool:
    return (mask & (1 << hour_idx)) != 0


# ======================================================================
#   AUSENTES DEL DÍA
# ======================================================================
async def _teachers_absent_that_day(
    session: AsyncSession, the_date: date
) -> Tuple[Set[int], Dict[int, int]]:

    q_abs = select(Absence).where(Absence.date == the_date)
    absences = (await session.execute(q_abs)).scalars().all()

    hours_by_teacher: Dict[int, int] = {}
    absent_ids: Set[int] = set()

    for a in absences:
        absent_ids.add(a.teacher_id)
        hours_by_teacher[a.teacher_id] = (
            hours_by_teacher.get(a.teacher_id, 0) | (a.hours_mask or 0)
        )

    q_leave = select(Leave).where(
        and_(
            Leave.start_date <= the_date,
            or_(Leave.end_date == None, Leave.end_date >= the_date),
        )
    )
    leaves = (await session.execute(q_leave)).scalars().all()

    FULL_MASK = make_mask_all()

    for lv in leaves:
        absent_ids.add(lv.teacher_id)
        hours_by_teacher[lv.teacher_id] = (
            hours_by_teacher.get(lv.teacher_id, 0) | FULL_MASK
        )

    return absent_ids, hours_by_teacher


# ======================================================================
#   PDF PRINCIPAL
# ======================================================================
async def build_daily_report_pdf(
    session: AsyncSession,
    the_date: date,
    path_out: str,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3,
):

    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    if absent_ids:
        q_teach = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: n for tid, n in (await session.execute(q_teach)).all()}
    else:
        name_by_id = {}

    styles = getSampleStyleSheet()
    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    data = [head]

    obs_lines: List[str] = []
    weekday_py = the_date.weekday()
    
    # ==========================================================
    #   MEJORA 2 — Ausentes con Guardia de RECREO
    # ==========================================================
    ausentes_guardia_recreo: List[str] = []

    for tid in absent_ids:
        slot = await get_teacher_slot(session, tid, weekday_py, recreo_index)
        if slot and slot.type == ScheduleType.GUARD:
            gtext = (slot.guard_type or "").upper()
            if gtext.startswith("G RECREO"):
                ausentes_guardia_recreo.append(name_by_id.get(tid))

    weekday_name = DAYS.get(weekday_py)

    # ===================================================================
    #   GENERAR FILAS
    # ===================================================================
    for label, hour_idx in HOUR_ROWS:

        # ===========================
        #   FILA RECREO — SPAN TOTAL
        # ===========================
        if hour_idx == recreo_index:
            data.append(["RECREO"] + [""] * 6)
            continue

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        # ------------------------
        #     AUSENTES
        # ------------------------
        for tid in sorted(absent_ids):

            # 1) Excluir titulares sustituidos
            t = await session.get(Teacher, tid)
            if t.status in (TeacherStatus.baja, TeacherStatus.excedencia):
                leave = (await session.execute(
                    select(Leave).where(
                        Leave.teacher_id == tid,
                        Leave.end_date.is_(None)
                    )
                )).scalar_one_or_none()

                if leave and leave.substitute_teacher_id:
                    continue

            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot:
                continue

            prof_name = name_by_id.get(tid)

            # ========================================
            #   MEJORA 1 → EXCLUIR SOLO CLASES ED
            # ========================================
            if slot.type == ScheduleType.CLASS:
                if (slot.group or "").upper() == "ED":
                    continue  # SOLO ED no aparece
                # Clase normal
                row_prof.append(prof_name)
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")
                continue

            # Guardias
            if slot.type == ScheduleType.GUARD:
                gtext = (slot.guard_type or "").upper()

                if gtext.startswith("G RECREO"):
                    # en recreo → NO va a la tabla, solo a Observaciones (ya añadido)
                    continue

                # guardia normal
                row_prof.append(prof_name)
                row_grp.append("guardia")
                row_room.append("guardia")
                row_subj.append("guardia")
                continue

        # ------------------------
        #   GUARDIAS (NO AUSENTES)
        # ------------------------
        guard_ids = await list_teachers_on_guard(
            session, weekday_py, hour_idx, absent_ids
        )

        guard_aliases = []
        for tid in guard_ids:
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot or slot.type != ScheduleType.GUARD:
                continue

            gtext = (slot.guard_type or "").upper()
            if gtext.startswith("G RECREO"):
                continue

            t = await session.get(Teacher, tid)
            guard_aliases.append(t.alias or t.name)

        def crush(xs: List[str]) -> str:
            return "\n".join([str(s) for s in xs if s.strip()])

        data.append([
            label,
            crush(row_prof),
            crush(row_grp),
            crush(row_room),
            crush(row_subj),
            "",
            crush(guard_aliases)
        ])

    # ===================================================================
    #   MAQUETACIÓN PDF
    # ===================================================================
    doc = SimpleDocTemplate(
        path_out,
        pagesize=A4,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
    )

    elements: List = []

    elements.append(
        Paragraph(
            f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
            styles["Title"],
        )
    )
    elements.append(Spacer(1, 6))


    # =======================================================
    #   OBSERVACIONES FIJAS ARRIBA A LA IZQUIERDA
    # =======================================================
    row_h = 72
    recreo_h = 44

    parts = []
    if ausentes_guardia_recreo:
        parts.append(
            "Ausentes Guardia Recreo: " + "; ".join(ausentes_guardia_recreo)
        )
    if observaciones_usuario:
        parts.append(observaciones_usuario)

    obs_text = "; ".join(parts) if parts else "—"

    total_width = A4[0] - doc.leftMargin - doc.rightMargin

    obs_table = Table(
        [[Paragraph(f"<b>Observaciones:</b><br/>{obs_text}", styles["Normal"])]],
        colWidths=[total_width],
        rowHeights=[row_h],
    )

    obs_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),   # ← fijo arriba izquierda
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    elements.append(obs_table)
    elements.append(Spacer(1,8))


    # =======================================================
    #   TABLA PRINCIPAL DEL PARTE DIARIO
    # =======================================================
    col_widths = [
        1.0 * cm,
        total_width * 0.32,
        total_width * 0.10,
        total_width * 0.10,
        total_width * 0.10,
        total_width * 0.18,
        total_width * 0.20,
    ]

    row_heights = [16] + [
        (recreo_h if idx == 3 else row_h)
        for idx in range(len(HOUR_ROWS))
    ]

    table = Table(data, colWidths=col_widths, rowHeights=row_heights)

    ts = TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),

        ("ALIGN", (0,0), (0,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),

        ("GRID", (0,0), (-1,-1), 0.5, colors.black),

        ("BACKGROUND", (0,4), (-1,4), colors.lightgrey),

        ("FONTSIZE", (0,1), (-1,-1), 8),

        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ])

    recreo_row_index = 1 + recreo_index
    ts.add("SPAN", (0, recreo_row_index), (-1, recreo_row_index))
    ts.add("ALIGN", (0, recreo_row_index), (-1, recreo_row_index), "CENTER")
    ts.add("VALIGN", (0, recreo_row_index), (-1, recreo_row_index), "MIDDLE")
    ts.add("FONTSIZE", (0, recreo_row_index), (-1, recreo_row_index), 12)

    table.setStyle(ts)

    elements.append(table)
    doc.build(elements)



# ======================================================================
#   HTML PREVIEW (idéntico en lógica al PDF)
# ======================================================================
async def build_daily_report_data(
    session: AsyncSession,
    the_date: date,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3,
):
    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    if absent_ids:
        q_teach = select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        name_by_id = {tid: n for tid, n in (await session.execute(q_teach)).all()}
    else:
        name_by_id = {}

    head = ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"]
    rows = []

    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py)

    # MEJORA 2 — Ausentes con Guardia de Recreo
    ausentes_guardia_recreo: List[str] = []
    for tid in absent_ids:
        slot = await get_teacher_slot(session, tid, weekday_py, recreo_index)
        if slot and slot.type == ScheduleType.GUARD:
            gtext = (slot.guard_type or "").upper()
            if gtext.startswith("G RECREO"):
                ausentes_guardia_recreo.append(name_by_id.get(tid))

    obs_lines: List[str] = []


    for label, hour_idx in HOUR_ROWS:

        if hour_idx == recreo_index:
            rows.append(["RECREO", "", "", "", "", "", ""])
            continue

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        # ------------------------
        #     AUSENTES
        # ------------------------
        for tid in sorted(absent_ids):

            t = await session.get(Teacher, tid)
            if t.status in (TeacherStatus.baja, TeacherStatus.excedencia):
                leave = (await session.execute(
                    select(Leave).where(
                        Leave.teacher_id == tid,
                        Leave.end_date.is_(None)
                    )
                )).scalar_one_or_none()
                if leave and leave.substitute_teacher_id:
                    continue

            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot:
                continue

            prof_name = name_by_id.get(tid)

            # MEJORA 1 — excluir solo ED
            if slot.type == ScheduleType.CLASS and (slot.group or "").upper() == "ED":
                continue

            # Guardias
            if slot.type == ScheduleType.GUARD:
                gtext = (slot.guard_type or "").upper()
                if gtext.startswith("G RECREO"):
                    # guardia recreo de ausente → observaciones
                    continue
                row_prof.append(prof_name)
                row_grp.append("guardia")
                row_room.append("guardia")
                row_subj.append("guardia")
                continue

            # Clase normal
            if slot.type == ScheduleType.CLASS:
                row_prof.append(prof_name)
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")

        # ------------------------
        #   GUARDIAS (NO AUSENTES)
        # ------------------------
        guard_ids = await list_teachers_on_guard(
            session, weekday_py, hour_idx, absent_ids
        )

        guard_aliases = []
        for tid in guard_ids:
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot or slot.type != ScheduleType.GUARD:
                continue

            gtext = (slot.guard_type or "").upper()
            if gtext.startswith("G RECREO"):
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

    # ==========================================================
    #   OBSERVACIONES
    # ==========================================================
    parts = []
    if ausentes_guardia_recreo:
        parts.append("Ausentes Guardia Recreo: " + "; ".join(ausentes_guardia_recreo))
    if observaciones_usuario:
        parts.append(observaciones_usuario)

    obs_text = "; ".join(parts) if parts else "—"

    return {
        "title": f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
        "weekday_name": weekday_name,
        "date_str": the_date.isoformat(),
        "head": head,
        "rows": rows,
        "obs_text": obs_text,
    }
