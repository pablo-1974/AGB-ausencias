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
#  CORE: Activar un profesor y desmontar correctamente la cadena
# ===================================================================
async def _activate_professor(session: AsyncSession, prof_id: int):
    """
    Activa prof_id como el ÚNICO profesor ACTIVO de la cadena.
    Todo lo que está por debajo pasa a EXPROFE.
    Los superiores permanecen en su estado (baja/excedencia).
    """

    teacher = await session.get(Teacher, prof_id)
    if not teacher:
        raise HTTPException(404, "Profesor no encontrado.")

    # 1) Activar este profesor
    teacher.status = TeacherStatus.activo

    # 2) Obtener cadena por debajo
    chain = await get_substitution_chain(session, prof_id)

    # 3) Degradar todo lo inferior
    cur = prof_id
    for sid in chain:
        sub = await session.get(Teacher, sid)
        if sub:
            sub.status = TeacherStatus.exprofe
            sub.titular = False

            flag = f"substitution:{cur}"
            slots = await session.scalars(
                select(ScheduleSlot).where(
                    and_(
                        ScheduleSlot.teacher_id == sid,
                        ScheduleSlot.source == flag
                    )
                )
            )
            for s in slots:
                await session.delete(s)

        cur = sid

    # 4) Romper sustitución del que activamos
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

    # Categoría opcional
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
            and_(
                ScheduleSlot.teacher_id == sub_id,
                ScheduleSlot.source == flag
            )
        )
    )
    for s in slots:
        await session.delete(s)

    await session.commit()


# ===================================================================
# Finalizar la BAJA COMPLETA (cierre en cascada)
# ===================================================================
async def close_leave_cascade(
    session: AsyncSession,
    teacher_id: int,
    end_date: date
) -> Leave:

    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        raise HTTPException(404, "No hay baja abierta para este profesor.")

    lv.end_date = end_date

    # Activar profesor (regla general)
    await _activate_professor(session, teacher_id)

    # SI ES TITULAR → desmontar toda la cadena
    prof = await session.get(Teacher, teacher_id)
    if prof.titular:
        chain = await get_substitution_chain(session, teacher_id)
        cur = teacher_id

        for sid in chain:
            sub = await session.get(Teacher, sid)
            sub.status = TeacherStatus.exprofe
            sub.titular = False

            flag = f"substitution:{cur}"
            slots = await session.scalars(
                select(ScheduleSlot).where(
                    and_(ScheduleSlot.teacher_id == sid, ScheduleSlot.source == flag)
                )
            )
            for s in slots:
                await session.delete(s)

            lv_s = await _get_open_leave(session, cur)
            if lv_s:
                lv_s.substitute_teacher_id = None
                lv_s.substitute_end_date = end_date

            cur = sid

    await session.commit()
    return lv


async def close_leave(session, teacher_id: int, end_date: date):
    return await close_leave_cascade(session, teacher_id, end_date)
