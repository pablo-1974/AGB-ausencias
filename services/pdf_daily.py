# services/pdf_daily.py — VERSIÓN FINAL
# Lógica nueva (sustituciones por fecha + guardias por fecha)
# + Maquetación profesional antigua (alturas, spans, paddings, tamaños, proporciones)

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
from services.schedule import get_teacher_slot, list_teachers_on_guard
from absences_router import make_mask_all

from utils import normalize_name


# ===========================================
# Tabla horarios
# ===========================================
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
#   AUSENTES DEL DÍA — FILTRADO POR FECHAS REALES
#   Regla: AUSENTE EFECTIVO SIN SUSTITUTO ACTIVO ESE DÍA
# ======================================================================
async def _teachers_absent_that_day(
    session: AsyncSession,
    the_date: date
) -> Tuple[Set[int], Dict[int, int]]:

    # --------------------------------------------------
    # Ausencias puntuales
    # --------------------------------------------------
    q_abs = select(Absence).where(Absence.date == the_date)
    absences = (await session.execute(q_abs)).scalars().all()

    hours_by_teacher: Dict[int, int] = {}
    absent_ids: Set[int] = set()

    for a in absences:
        absent_ids.add(a.teacher_id)
        hours_by_teacher[a.teacher_id] = (
            hours_by_teacher.get(a.teacher_id, 0) | (a.hours_mask or 0)
        )

    # --------------------------------------------------
    # Bajas jerárquicas vigentes ese día
    # --------------------------------------------------
    q_leave = select(Leave).where(
        and_(
            Leave.start_date <= the_date,
            or_(Leave.end_date.is_(None), Leave.end_date >= the_date)
        )
    )
    leaves = (await session.execute(q_leave)).scalars().all()

    if not leaves:
        return absent_ids, hours_by_teacher

    # Construir árbol por parent_leave_id
    by_parent: Dict[int | None, List[Leave]] = {}
    for lv in leaves:
        by_parent.setdefault(lv.parent_leave_id, []).append(lv)

    FULL_MASK = make_mask_all()

    def deepest_active(leave: Leave, depth: int = 0) -> Tuple[int, Leave]:
        best = (depth, leave)
        for child in by_parent.get(leave.id, []):
            cand = deepest_active(child, depth + 1)
            if cand[0] > best[0]:
                best = cand
        return best

    # Procesar solo bajas raíz
    root_leaves = by_parent.get(None, [])

    for root in root_leaves:
        _, leaf = deepest_active(root)
        teacher = await session.get(Teacher, leaf.teacher_id)
        if not teacher:
            continue

        # ✅ AUSENTE SOLO SI ESTÁ REALMENTE DE BAJA O EXCEDENCIA
        if teacher.status in (TeacherStatus.baja, TeacherStatus.excedencia):
            absent_ids.add(teacher.id)
            hours_by_teacher[teacher.id] = (
                hours_by_teacher.get(teacher.id, 0) | FULL_MASK
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

    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py)

    # Ausentes de guardia de recreo
    ausentes_guardia_recreo: List[str] = []
    for tid in absent_ids:
        slot = await get_teacher_slot(session, tid, weekday_py, recreo_index)
        if slot and slot.type == ScheduleType.GUARD:
            if (slot.guard_type or "").upper().startswith("G RECREO"):
                ausentes_guardia_recreo.append(name_by_id.get(tid))

    # ============================================================
    #   GENERAR FILAS
    # ============================================================
    for label, hour_idx in HOUR_ROWS:

        if hour_idx == recreo_index:
            data.append(["RECREO"] + [""] * 6)
            continue

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        # AUSENTES
        for tid in sorted(absent_ids, key=lambda tid: normalize_name(name_by_id.get(tid, ""))):

            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot:
                continue

            prof_name = name_by_id.get(tid)

            if slot.type == ScheduleType.CLASS:
                if (slot.group or "").upper() == "ED":
                    continue
                row_prof.append(prof_name)
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")
                continue

            if slot.type == ScheduleType.GUARD:
                g = (slot.guard_type or "").upper()
                if g.startswith("G RECREO"):
                    continue
                row_prof.append(prof_name)
                row_grp.append("guardia")
                row_room.append("guardia")
                row_subj.append("guardia")
                continue

        # GUARDIAS ACTIVOS
        guard_ids = await list_teachers_on_guard(
            session, weekday_py, hour_idx, absent_ids
        )

        guard_aliases = []

        for tid in guard_ids:
            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot or slot.type != ScheduleType.GUARD:
                continue

            if (slot.guard_type or "").upper().startswith("G RECREO"):
                continue

            teacher = await session.get(Teacher, tid)
            if not teacher:
                continue

            guard_aliases.append(teacher.alias or teacher.name)

        def crush(xs: List[str]) -> str:
            return "\n".join([x for x in xs if x.strip()])

        data.append([
            label,
            crush(sorted(row_prof, key=normalize_name)),
            crush(sorted(row_grp)),
            crush(sorted(row_room)),
            crush(sorted(row_subj)),
            "",
            crush(sorted(guard_aliases, key=normalize_name)),
        ])

    # =========================
    # PDF (maquetación intacta)
    # =========================
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

    parts = []
    if ausentes_guardia_recreo:
        parts.append("Ausentes Guardia Recreo: " + "; ".join(ausentes_guardia_recreo))
    if observaciones_usuario:
        parts.append(observaciones_usuario)

    obs_text = "; ".join(parts) if parts else "—"

    total_width = A4[0] - doc.leftMargin - doc.rightMargin

    obs_table = Table(
        [[Paragraph(f"<b>Observaciones:</b><br/>{obs_text}", styles["Normal"])]],
        colWidths=[total_width],
        rowHeights=[72],
    )

    obs_table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ]))

    elements.append(obs_table)
    elements.append(Spacer(1, 8))

    col_widths = [
        1.0 * cm,
        total_width * 0.32,
        total_width * 0.10,
        total_width * 0.10,
        total_width * 0.10,
        total_width * 0.18,
        total_width * 0.20,
    ]

    row_heights = [16] + [72 for _ in HOUR_ROWS]

    table = Table(data, colWidths=col_widths, rowHeights=row_heights)

    ts = TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("ALIGN", (0,0), (0,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("FONTSIZE", (0,1), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
    ])

    table.setStyle(ts)
    elements.append(table)
    doc.build(elements)

# ======================================================================
#   HTML PREVIEW — MISMA LÓGICA QUE EL PDF
# ======================================================================
async def build_daily_report_data(
    session: AsyncSession,
    the_date: date,
    observaciones_usuario: str | None = None,
    recreo_index: int = 3,
):
    absent_ids, hours_by_teacher = await _teachers_absent_that_day(session, the_date)

    if absent_ids:
        q = await session.execute(
            select(Teacher.id, Teacher.name).where(Teacher.id.in_(absent_ids))
        )
        name_by_id = {tid: n for tid, n in q.all()}
    else:
        name_by_id = {}

    rows = []
    weekday_py = the_date.weekday()
    weekday_name = DAYS.get(weekday_py)

    for label, hour_idx in HOUR_ROWS:
        if hour_idx == recreo_index:
            rows.append(["RECREO", "", "", "", "", "", ""])
            continue

        row_prof, row_grp, row_room, row_subj = [], [], [], []

        for tid in sorted(absent_ids, key=lambda t: normalize_name(name_by_id.get(t, ""))):
            mask = hours_by_teacher.get(tid, 0)
            if not _is_absent(mask, hour_idx):
                continue

            slot = await get_teacher_slot(session, tid, weekday_py, hour_idx)
            if not slot:
                continue

            if slot.type == ScheduleType.CLASS:
                if (slot.group or "").upper() == "ED":
                    continue
                row_prof.append(name_by_id.get(tid))
                row_grp.append(slot.group or "")
                row_room.append(slot.room or "")
                row_subj.append(slot.subject or "")
            else:
                row_prof.append(name_by_id.get(tid))
                row_grp.append("guardia")
                row_room.append("guardia")
                row_subj.append("guardia")

        rows.append([
            label,
            "\n".join(row_prof),
            "\n".join(row_grp),
            "\n".join(row_room),
            "\n".join(row_subj),
            "",
            "",
        ])

    return {
        "title": f"Ausencias del día ({weekday_name} {the_date.strftime('%d/%m/%Y')})",
        "weekday_name": weekday_name,
        "date_str": the_date.isoformat(),
        "head": ["HORA", "PROFESOR", "GRUPO", "AULA", "ASIGN.", "FIRMAS", "GUARDIA"],
        "rows": rows,
        "observaciones": observaciones_usuario or "",
    }
