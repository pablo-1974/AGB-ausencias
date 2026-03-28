# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from fastapi import HTTPException

from models import Leave, Teacher, TeacherStatus, ScheduleSlot


# -------------------------------------------------------------------
# Helper: obtener la baja ACTIVA de un profesor (si existe)
# -------------------------------------------------------------------
async def _get_open_leave(session: AsyncSession, teacher_id: int) -> Optional[Leave]:
    return await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None)
            )
        )
    )


# -------------------------------------------------------------------
# Helper: obtener sustituto directo del profesor
# -------------------------------------------------------------------
async def _get_direct_substitute(session: AsyncSession, teacher_id: int) -> Optional[int]:
    lv = await _get_open_leave(session, teacher_id)
    return lv.substitute_teacher_id if lv else None


# -------------------------------------------------------------------
# Helper: obtener TODA la cadena de sustitución (sub1, sub2, sub3…)
# -------------------------------------------------------------------
async def get_substitution_chain(session: AsyncSession, teacher_id: int) -> List[int]:
    chain = []
    cur = teacher_id

    while True:
        nxt = await _get_direct_substitute(session, cur)
        if not nxt:
            break
        chain.append(nxt)
        cur = nxt

    return chain


# ===================================================================
# CORE: Activar un profesor y desmontar correctamente la cadena
# ===================================================================
async def _activate_professor(session: AsyncSession, prof_id: int):
    teacher = await session.get(Teacher, prof_id)
    if not teacher:
        raise HTTPException(404, "Profesor no encontrado.")

    # Activar profesor
    teacher.status = TeacherStatus.activo

    # Degradar sustitutos por debajo
    chain = await get_substitution_chain(session, prof_id)

    cur = prof_id
    for sid in chain:
        sub = await session.get(Teacher, sid)
        if sub:
            sub.status = TeacherStatus.exprofe
            sub.titular = False

            # Borrar slots clonados
            flag = f"substitution:{cur}"
            slots = await session.scalars(
                select(ScheduleSlot).where(
                    and_(ScheduleSlot.teacher_id == sid, ScheduleSlot.source == flag)
                )
            )
            for s in slots:
                await session.delete(s)

        cur = sid

    # Romper sustitución del activado
    lv = await _get_open_leave(session, prof_id)
    if lv:
        lv.substitute_teacher_id = None
        lv.substitute_end_date = date.today()

    await session.commit()


# ===================================================================
# Abrir una baja
# ===================================================================
async def open_leave(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    leave_type: TeacherStatus,
    cause: str,
    category: Optional[str] = None,
) -> Leave:

    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(400, "Tipo de baja no válido.")

    teacher = await session.get(Teacher, teacher_id)
    if not teacher or teacher.status != TeacherStatus.activo:
        raise HTTPException(400, "Solo se puede iniciar baja a un profesor activo.")

    if leave_type == TeacherStatus.baja:
        if category and category not in list("ABCDEFGHIJKL"):
            raise HTTPException(400, "Categoría inválida.")
        category = category or None
    else:
        category = None

    existing = await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                or_(Leave.end_date == None, Leave.end_date >= start_date)
            )
        )
    )

    if existing:
        if existing.start_date > start_date:
            existing.start_date = start_date
        existing.cause = cause
        existing.category = category
        teacher.status = leave_type
        await session.commit()
        return existing

    lv = Leave(
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category
    )

    session.add(lv)
    teacher.status = leave_type

    await session.commit()
    return lv


# ===================================================================
# Asignar sustituto
# ===================================================================
async def set_substitution(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    substitute_teacher_id: int,
) -> Leave:

    lv = await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.start_date <= start_date,
                or_(Leave.end_date == None, Leave.end_date >= start_date)
            )
        )
    )

    if not lv:
        lv = await open_leave(
            session,
            teacher_id,
            start_date,
            TeacherStatus.baja,
            "Alta automática de sustitución"
        )

    lv.substitute_teacher_id = substitute_teacher_id
    lv.substitute_start_date = start_date
    lv.substitute_end_date = None

    sub = await session.get(Teacher, substitute_teacher_id)
    sub.status = TeacherStatus.activo
    sub.titular = False

    await session.commit()
    return lv


# ===================================================================
# Finalizar solo la sustitución
# ===================================================================
async def end_substitution(
    session: AsyncSession,
    teacher_id: int,
    end_date: date
):
    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        raise HTTPException(404, "No hay baja abierta.")

    sub_id = lv.substitute_teacher_id
    if not sub_id:
        return

    lv.substitute_end_date = end_date
    lv.substitute_teacher_id = None

    sub = await session.get(Teacher, sub_id)
    sub.status = TeacherStatus.exprofe
    sub.titular = False

    flag = f"substitution:{teacher_id}"
    slots = await session.scalars(
        select(ScheduleSlot).where(
            and_(ScheduleSlot.teacher_id == sub_id, ScheduleSlot.source == flag)
        )
    )
    for s in slots:
        await session.delete(s)

    await session.commit()


# ===================================================================
# Finalizar BAJA COMPLETA (cierre en cascada)
# ===================================================================
# ===================================================================
# Finalizar BAJA COMPLETA (cierre REAL en cascada)
# ===================================================================
async def close_leave_cascade(
    session: AsyncSession,
    teacher_id: int,
    end_date: date
) -> Leave:

    # 1) Obtener la baja del profesor principal
    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        raise HTTPException(404, "No hay baja abierta para este profesor.")

    lv.end_date = end_date

    # 2) Obtener cadena completa: titular + sustitutos
    chain = [teacher_id] + await get_substitution_chain(session, teacher_id)

    # 3) Cerrar bajas de TODOS los profesores en la cadena
    for tid in chain:
        l = await _get_open_leave(session, tid)
        if l:
            l.end_date = end_date
            l.substitute_teacher_id = None
            l.substitute_end_date = end_date

    # 4) Cambiar estados correctamente
    # Titular → activo
    titular = await session.get(Teacher, teacher_id)
    titular.status = TeacherStatus.activo
    titular.titular = True

    # Sustitutos → exprofe
    for tid in chain[1:]:
        sub = await session.get(Teacher, tid)
        if sub:
            sub.status = TeacherStatus.exprofe
            sub.titular = False

            # 5) Borrar horarios clonados
            flag = f"substitution:{teacher_id}"
            slots = await session.scalars(
                select(ScheduleSlot).where(
                    and_(ScheduleSlot.teacher_id == tid,
                         ScheduleSlot.source.contains("substitution:"))
                )
            )
            for s in slots:
                await session.delete(s)

    # 6) Limpiar “activos fantasma”
    old_subs = await session.execute(
        select(Teacher).where(
            and_(
                Teacher.titular == False,
                Teacher.status == TeacherStatus.activo
            )
        )
    )
    for t in old_subs.scalars():
        t.status = TeacherStatus.exprofe
        t.titular = False

    await session.commit()
    return lv


async def close_leave(session, teacher_id: int, end_date: date):
    return await close_leave_cascade(session, teacher_id, end_date)
