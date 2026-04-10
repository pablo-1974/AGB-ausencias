# ============================================================
# services/leaves.py — Lógica interna de bajas jerárquicas
# ============================================================

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

async def _get_open_leave(
    session: AsyncSession,
    teacher_id: int
) -> Optional[Leave]:
    """Devuelve cualquier baja activa (raíz o hija) del profesor."""
    return await session.scalar(
        select(Leave).where(
            Leave.teacher_id == teacher_id,
            Leave.end_date.is_(None)
        )
    )


async def _get_active_substitution_leave(
    session: AsyncSession,
    teacher_id: int
) -> Optional[Leave]:
    """
    Devuelve la baja activa en la que el profesor actúa como sustituto,
    si existe.
    """
    return await session.scalar(
        select(Leave).where(
            Leave.teacher_id == teacher_id,
            Leave.parent_leave_id.is_not(None),
            Leave.end_date.is_(None)
        )
    )


async def _get_leave_by_id(
    session: AsyncSession,
    leave_id: int
) -> Optional[Leave]:
    """Obtiene una baja por ID."""
    return await session.get(Leave, leave_id)


async def _get_children_leaves(
    session: AsyncSession,
    parent_leave_id: int
) -> List[Leave]:
    """Devuelve las bajas hijas directas."""
    res = await session.execute(
        select(Leave).where(Leave.parent_leave_id == parent_leave_id)
    )
    return list(res.scalars().all())


async def _get_cascade_children(
    session: AsyncSession,
    leave_id: int
) -> List[Leave]:
    """Devuelve todas las bajas descendientes."""
    result: List[Leave] = []
    stack = [leave_id]

    while stack:
        current = stack.pop()
        children = await _get_children_leaves(session, current)
        for child in children:
            result.append(child)
            stack.append(child.id)

    return result


def _validate_close_date(
    *,
    start_date: date,
    end_date: date,
    today: date,
    max_child_start: Optional[date] = None
):
    """Valida coherencia temporal al cerrar una baja."""
    if end_date < start_date:
        raise HTTPException(
            400,
            "La fecha de fin no puede ser anterior al inicio de la baja."
        )

    if end_date > today:
        raise HTTPException(
            400,
            "La fecha de fin no puede estar en el futuro."
        )

    if max_child_start and end_date < max_child_start:
        raise HTTPException(
            400,
            "La fecha de fin es anterior al inicio de una sustitución dependiente."
        )


# ============================================================
# CADENA DE SUSTITUCIÓN
# ============================================================

async def get_substitution_chain(
    session: AsyncSession,
    teacher_id: int
) -> List[int]:
    """Devuelve la cadena completa de sustituciones."""
    root = await _get_open_leave(session, teacher_id)
    if not root:
        return []

    chain: List[int] = []
    stack = [root.id]

    while stack:
        lid = stack.pop()
        children = await _get_children_leaves(session, lid)
        for ch in children:
            chain.append(ch.teacher_id)
            stack.append(ch.id)

    return chain


# ============================================================
# BORRAR HORARIO CLONADO
# ============================================================

async def _delete_cloned_slots(
    session: AsyncSession,
    teacher_id: int
):
    slots = await session.scalars(
        select(ScheduleSlot).where(
            ScheduleSlot.teacher_id == teacher_id,
            ScheduleSlot.source.contains("substitution:")
        )
    )
    for s in slots:
        await session.delete(s)


# ============================================================
# ABRIR BAJA (RAÍZ O HIJA)
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
    """
