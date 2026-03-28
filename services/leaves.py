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
    """Devuelve la baja activa (sin end_date) de un profesor."""
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
    """Devuelve las bajas hijas directas (sustitutos)."""
    res = await session.execute(
        select(Leave).where(Leave.parent_leave_id == parent_leave_id)
    )
    return list(res.scalars().all())


async def _get_cascade_children(session: AsyncSession, leave_id: int) -> List[Leave]:
    """
    Devuelve TODAS las bajas descendientes en cascada:
    padre → hijos → nietos → ...
    """
    result: List[Leave] = []
    stack = [leave_id]

    while stack:
        current = stack.pop()
        children = await _get_children_leaves(session, current)

        for child in children:
            result.append(child)
            stack.append(child.id)

    return result


async def _delete_cloned_slots(session: AsyncSession, teacher_id: int):
    """Elimina slots clonados creados por sustituciones."""
    slots = await session.scalars(
        select(ScheduleSlot).where(
            and_(
                ScheduleSlot.teacher_id == teacher_id,
                ScheduleSlot.source.contains("substitution:")
            )
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
    parent_leave_id: Optional[int] = None,
) -> Leave:

    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(400, "Tipo de baja no válido.")

    teacher = await session.get(Teacher, teacher_id)
    if teacher is None or teacher.status != TeacherStatus.activo:
        raise HTTPException(400, "Solo se puede iniciar baja a un profesor activo.")

    # No puede tener otra baja activa
    if await _get_open_leave(session, teacher_id):
        raise HTTPException(400, "Este profesor ya tiene una baja activa.")

    lv = Leave(
        teacher_id=teacher_id,
        parent_leave_id=parent_leave_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category,
        substitute_teacher_id=None,
        substitute_start_date=None,
        substitute_end_date=None,
    )

    session.add(lv)

    # Cambiar estado del profesor
    teacher.status = leave_type
    teacher.titular = (parent_leave_id is None)  # Titular si es baja raíz

    await session.commit()
    return lv


# ============================================================
# CREAR SUSTITUCIÓN → CREA BAJA HIJA
# ============================================================
async def set_substitution(
    session: AsyncSession,
    teacher_id: int,          # profesor sustituido
    start_date: date,
    substitute_teacher_id: int,
) -> Leave:

    parent_leave = await _get_open_leave(session, teacher_id)
    if not parent_leave:
        raise HTTPException(404, "El profesor no tiene baja abierta.")

    # Crear baja hija para el sustituto
    sub_leave = await open_leave(
        session=session,
        teacher_id=substitute_teacher_id,
        start_date=start_date,
        leave_type=TeacherStatus.baja,
        cause="Sustitución",
        category=None,
        parent_leave_id=parent_leave.id
    )

    # Datos decorativos (para vistas)
    parent_leave.substitute_teacher_id = substitute_teacher_id
    parent_leave.substitute_start_date = start_date
    parent_leave.substitute_end_date = None

    await session.commit()
    return sub_leave


# ============================================================
# CIERRE EN CASCADA
# ============================================================
async def close_leave_cascade(
    session: AsyncSession,
    leave_id: int,
    end_date: date
) -> Leave:

    leave = await _get_leave_by_id(session, leave_id)
    if not leave:
        raise HTTPException(404, "La baja no existe.")

    if leave.end_date is not None:
        raise HTTPException(400, "La baja ya está cerrada.")

    # Cerrar baja raíz
    leave.end_date = end_date
    leave.substitute_teacher_id = None
    leave.substitute_end_date = end_date

    # Obtener TODAS las bajas hijas y nietas
    descendants = await _get_cascade_children(session, leave_id)

    # Cerrar todas las bajas descendientes
    for ch in descendants:
        if ch.end_date is None:
            ch.end_date = end_date
        ch.substitute_teacher_id = None
        ch.substitute_end_date = end_date

    # Restaurar estado del profesor raíz
    root_prof = await session.get(Teacher, leave.teacher_id)
    root_prof.status = TeacherStatus.activo
    root_prof.titular = True

    # Todos los sustitutos → exprofes
    for ch in descendants:
        prof = await session.get(Teacher, ch.teacher_id)
        prof.status = TeacherStatus.exprofe
        prof.titular = False
        await _delete_cloned_slots(session, prof.id)

    await session.commit()
    return leave


# ============================================================
# ALIAS SIMPLE
# ============================================================
async def close_leave(session: AsyncSession, leave_id: int, end_date: date):
    return await close_leave_cascade(session, leave_id, end_date)
