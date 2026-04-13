# services/stats_recount.py

from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional, Iterable

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
    Itera solo por días lectivos reales (excluye fines de semana y festivos).
    """
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and not is_holiday(cur, cal):
            yield cur
        cur += timedelta(days=1)


# ============================================================
# Servicio principal
# ============================================================

async def get_stats_recount(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    teacher_id: Optional[int] = None,
    tipo: str = "both",          # "absences" | "leaves" | "both"
    categoria: str = "ALL",      # "ALL" | "A".."L"
):
    """
    Devuelve el recuento administrativo agregado para estadísticas.

    Agrupación EXACTA:
        (profesor, tipo, causa)

    Columnas:
        Profesor | Tipo | Causa | Días

    Reglas:
    - Ordenación alfabética (española)
    - Categoría Z excluida SIEMPRE
    - Excedencias excluidas
    - Leaves técnicos de sustitución excluidos
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
    # 2) Preparar acumulador
    #    clave: (teacher_id, tipo, categoria)
    # --------------------------------------------------------
    acc = defaultdict(int)

    # --------------------------------------------------------
    # 3) AUSENCIAS PUNTUALES
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

        if teacher_id:
            q_abs = q_abs.where(Absence.teacher_id == teacher_id)

        if categoria != "ALL":
            q_abs = q_abs.where(Absence.category == categoria)

        res_abs = await session.execute(q_abs)

        for a in res_abs.scalars():
            # excluir días no lectivos
            if a.date.weekday() >= 5 or is_holiday(a.date, cal):
                continue

            acc[(a.teacher_id, "Ausencia", a.category)] += 1

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

        if categoria != "ALL":
            q_lv = q_lv.where(Leave.category == categoria)

        res_lv = await session.execute(q_lv)

        for lv in res_lv.scalars():
            teacher = await session.get(Teacher, lv.teacher_id)
            if not teacher:
                continue

            # ❌ Excluir excedencias
            if teacher.status == TeacherStatus.excedencia:
                continue

            # ❌ Excluir leaves técnicos de sustitución
            if lv.is_substitution:
                continue

            # Ajustar rango efectivo
            eff_from = max(date_from, lv.start_date)
            eff_to = lv.end_date or date_to
            eff_to = min(eff_to, date_to)

            for day in iter_lective_days(eff_from, eff_to, cal):
                acc[(teacher.id, "Baja", lv.category)] += 1

    # --------------------------------------------------------
    # 5) Resolver nombres de profesores
    # --------------------------------------------------------
    if not acc:
        return []

    teacher_ids = {k[0] for k in acc.keys()}

    q_teachers = await session.execute(
        select(Teacher.id, Teacher.name).where(Teacher.id.in_(teacher_ids))
    )
    name_by_id = {tid: name for tid, name in q_teachers.all()}

    # --------------------------------------------------------
    # 6) Construir filas finales
    # --------------------------------------------------------
    rows = []

    for (tid, tipo_txt, cat), days in acc.items():
        rows.append({
            "teacher": name_by_id.get(tid, f"ID {tid}"),
            "type": tipo_txt,
            "category": cat,
            "days": days,
        })

    # --------------------------------------------------------
    # 7) Ordenación administrativa
    # --------------------------------------------------------
    rows.sort(
        key=lambda r: (
            normalize_name(r["teacher"]),
            r["type"],
            r["category"],
        )
    )

    return rows
