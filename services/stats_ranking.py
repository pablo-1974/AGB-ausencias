# services/stats_ranking.py

from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import (
    Absence,
    Leave,
    Teacher,
    TeacherStatus,
    SchoolCalendar,
)
from utils import normalize_name


# ============================================================
# Auxiliares de calendario
# ============================================================

def is_holiday(day: date, cal: SchoolCalendar) -> bool:
    """
    Devuelve True si el día NO es lectivo según el calendario escolar.
    """
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


def iter_lective_days(start: date, end: date, cal: SchoolCalendar) -> Iterable[date]:
    """
    Itera únicamente por días lectivos reales.
    """
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and not is_holiday(cur, cal):
            yield cur
        cur += timedelta(days=1)


# ============================================================
# Servicio principal: RANKING
# ============================================================

async def get_stats_ranking(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    tipo: str = "both",   # "absences" | "leaves" | "both"
):
    """
    Devuelve el ranking de profesorado por días de ausencia/baja.

    Columnas:
        Profesor | Días

    Reglas:
    - Se agrupa SOLO por profesor
    - Se excluye SIEMPRE categoría Z
    - Se excluyen excedencias y bajas técnicas
    - Ordenación:
        1) días DESC
        2) nombre del profesor (orden español) como desempate
    """

    # --------------------------------------------------------
    # 1) Obtener calendario escolar activo
    # --------------------------------------------------------
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc()).limit(1)
        )
    ).scalar_one_or_none()

    if not cal:
        return []

    # --------------------------------------------------------
    # 2) Acumulador por profesor
    # --------------------------------------------------------
    acc = defaultdict(int)  # teacher_id -> total_dias

    # --------------------------------------------------------
    # 3) AUSENCIAS
    # --------------------------------------------------------
    if tipo in ("absences", "both"):

        q_abs = select(Absence).where(
            and_(
                Absence.date >= date_from,
                Absence.date <= date_to,
                Absence.category.is_not(None),
                Absence.category != "Z",
            )
        )

        res_abs = await session.execute(q_abs)

        for a in res_abs.scalars():
            if a.date.weekday() >= 5 or is_holiday(a.date, cal):
                continue

            acc[a.teacher_id] += 1

    # --------------------------------------------------------
    # 4) BAJAS
    # --------------------------------------------------------
    if tipo in ("leaves", "both"):

        q_lv = select(Leave).where(
            and_(
                Leave.start_date <= date_to,
                or_(Leave.end_date.is_(None), Leave.end_date >= date_from),
                Leave.category.is_not(None),
                Leave.category != "Z",
            )
        )

        res_lv = await session.execute(q_lv)

        for lv in res_lv.scalars():
            teacher = await session.get(Teacher, lv.teacher_id)
            if not teacher:
                continue

            # ❌ Excluir excedencias
            if teacher.status == TeacherStatus.excedencia:
                continue

            # ❌ Excluir bajas técnicas de sustitución
            if lv.is_substitution:
                continue

            eff_from = max(date_from, lv.start_date)
            eff_to = lv.end_date or date_to
            eff_to = min(eff_to, date_to)

            for _day in iter_lective_days(eff_from, eff_to, cal):
                acc[teacher.id] += 1

    # --------------------------------------------------------
    # 5) Resolver nombres de profesores
    # --------------------------------------------------------
    if not acc:
        return []

    teacher_ids = set(acc.keys())

    q_teachers = await session.execute(
        select(Teacher.id, Teacher.name).where(Teacher.id.in_(teacher_ids))
    )
    name_by_id = {tid: name for tid, name in q_teachers.all()}

    # --------------------------------------------------------
    # 6) Construir filas
    # --------------------------------------------------------
    rows = []

    for tid, days in acc.items():
        rows.append({
            "teacher": name_by_id.get(tid, f"ID {tid}"),
            "days": days,
        })

    # --------------------------------------------------------
    # 7) Ordenación: ranking real
    # --------------------------------------------------------
    rows.sort(
        key=lambda r: (
            -r["days"],              # ranking por días
            normalize_name(r["teacher"]),  # desempate alfabético
        )
    )

    return rows
