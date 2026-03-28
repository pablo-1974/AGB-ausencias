# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import HTTPException

from models import Leave, Teacher, TeacherStatus, ScheduleSlot


# ============================================================
# HELPERS
# ============================================================

async def _get_open_leave(session: AsyncSession, teacher_id: int) -> Optional[Leave]:
    return await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None)
            )
        )
    )


async def _get_leave_by_id(session: AsyncSession, leave_id: int) -> Optional[Leave]:
    return await session.get(Leave, leave_id)


async def _get_children_leaves(session: AsyncSession, parent_leave_id: int) -> List[Leave]:
    res = await session.execute(
        select(Leave).where(Leave.parent_leave_id == parent_leave_id)
    )
    return list(res.scalars().all())


async def _get_cascade_children(session: AsyncSession, leave_id: int) -> List[Leave]:
    """Devuelve TODAS las bajas descendientes."""
    result = []
    stack = [leave_id]

    while stack:
        current = stack.pop()
        children = await _get_children_leaves(session, current)
        for child in children:
            result.append(child)
            stack.append(child.id)

    return result


async def _delete_cloned_slots(session: AsyncSession, tid: int):
    slots = await session.scalars(
        select(ScheduleSlot).where(
            ScheduleSlot.teacher_id == tid,
            ScheduleSlot.source.contains("substitution:")
        )
    )
    for s in slots:
        await session.delete(s)


# ============================================================
# ABRIR BAJA
# ============================================================
async def open_leave(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    leave_type: TeacherStatus,
    cause: str,
    category: Optional[str] = None,
    parent_leave_id: Optional[int] = None
) -> Leave:

    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(400, "Tipo de baja no válido.")

    teacher = await session.get(Teacher, teacher_id)
    if not teacher or teacher.status != TeacherStatus.activo:
        raise HTTPException(400, "Solo se puede iniciar baja a un profesor activo.")

    if await _get_open_leave(session, teacher_id):
        raise HTTPException(400, "Ya hay una baja activa.")

    lv = Leave(
        teacher_id=teacher_id,
        parent_leave_id=parent_leave_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category
    )

    session.add(lv)
    teacher.status = leave_type
    teacher.titular = (parent_leave_id is None)  # titular si no es sustituto
    await session.commit()
    return lv


# ============================================================
# SUSTITUCIÓN → CREA BAJA HIJA AUTOMÁTICA
# ============================================================
async def set_substitution(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    substitute_teacher_id: int,
) -> Leave:

    # Baja padre
    parent_leave = await _get_open_leave(session, teacher_id)
    if not parent_leave:
        raise HTTPException(404, "El profesor sustituido no tiene baja abierta.")

    # Baja hija del sustituto
    sub_leave = await open_leave(
        session=session,
        teacher_id=substitute_teacher_id,
        start_date=start_date,
        leave_type=TeacherStatus.baja,
        cause="Sustitución",
        category=None,
        parent_leave_id=parent_leave.id
    )

    # Datos visuales
    parent_leave.substitute_teacher_id = substitute_teacher_id
    parent_leave.substitute_start_date = start_date
    parent_leave.substitute_end_date = None

    await session.commit()
    return sub_leave


# ============================================================
# FINALIZAR BAJA EN CASCADA
# ============================================================
async def close_leave_cascade(
    session: AsyncSession,
    leave_id: int,
    end_date: date
) -> Leave:

    # Baja a cerrar
    leave = await _get_leave_by_id(session, leave_id)
    if not leave or leave.end_date is not None:
        raise HTTPException(404, "La baja ya está cerrada o no existe.")

    leave.end_date = end_date

    # TODA la cadena de hijos
    children = await _get_cascade_children(session, leave_id)

    # Cerrar hijos
    for ch in children:
        ch.end_date = end_date
        ch.substitute_teacher_id = None
        ch.substitute_end_date = end_date

    # Restaurar profesor del leave superior
    prof = await session.get(Teacher, leave.teacher_id)
    prof.status = TeacherStatus.activo
    prof.titular = True

    # Hijos → exprofe
    for ch in children:
        prof_sub = await session.get(Teacher, ch.teacher_id)
        prof_sub.status = TeacherStatus.exprofe
        prof_sub.titular = False
        await _delete_cloned_slots(session, prof_sub.id)

    # limpiar sustituciones visuales
    leave.substitute_teacher_id = None
    leave.substitute_end_date = end_date

    await session.commit()
    return leave


async def close_leave(session, leave_id: int, end_date: date):
    return await close_leave_cascade(session, leave_id, end_date)
