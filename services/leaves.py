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
    """Devuelve cualquier baja activa (raíz o hija)."""
    return await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None)
            )
        )
    )


async def _get_leave_by_id(session: AsyncSession, leave_id: int) -> Optional[Leave]:
    """Obtiene una baja por ID."""
    return await session.get(Leave, leave_id)


async def _get_children_leaves(session: AsyncSession, parent_leave_id: int) -> List[Leave]:
    """Devuelve las bajas hijas directas (sustitutos)."""
    res = await session.execute(
        select(Leave).where(Leave.parent_leave_id == parent_leave_id)
    )
    return list(res.scalars().all())


async def _get_cascade_children(session: AsyncSession, leave_id: int) -> List[Leave]:
    """
    Devuelve TODAS las bajas descendientes:
    leave_padre → hijos → nietos → ...
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


def _validate_close_date(
    *,
    start_date: date,
    end_date: date,
    today: date,
    max_child_start: date | None = None
):
    """
    Valida que la fecha de cierre sea coherente con la baja y su jerarquía.
    """
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
# ✅ FUNCIÓN: CADENA DE SUSTITUCIÓN
# ============================================================

async def get_substitution_chain(session: AsyncSession, teacher_id: int) -> List[int]:
    """
    Devuelve la cadena completa de sustituciones de un profesor:
    titular → sustituto → sustituto del sustituto → ...
    """
    root_leave = await _get_open_leave(session, teacher_id)
    if not root_leave:
        return []

    chain: List[int] = []
    stack = [root_leave.id]

    while stack:
        current_leave_id = stack.pop()
        children = await _get_children_leaves(session, current_leave_id)

        for child in children:
            chain.append(child.teacher_id)
            stack.append(child.id)

    return chain


# ============================================================
# BORRAR HORARIO CLONADO CUANDO TERMINA SUSTITUCIÓN
# ============================================================

async def _delete_cloned_slots(session: AsyncSession, teacher_id: int):
    """Elimina slots clonados de sustitución."""
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
    Crea una baja nueva.
    """

    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(400, "Tipo de baja no válido.")

    teacher = await session.get(Teacher, teacher_id)
    if not teacher or teacher.status != TeacherStatus.activo:
        raise HTTPException(400, "Solo se puede iniciar baja a un profesor activo.")

    if parent_leave_id is None:
        existing_root = await session.scalar(
            select(Leave).where(
                Leave.teacher_id == teacher_id,
                Leave.parent_leave_id.is_(None),
                Leave.end_date.is_(None)
            )
        )
        if existing_root:
            raise HTTPException(400, "Este profesor ya tiene una baja raíz activa.")

    lv = Leave(
        teacher_id=teacher_id,
        parent_leave_id=parent_leave_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category,
        substitute_teacher_id=None,
        substitute_start_date=None,
        substitute_end_date=None
    )

    session.add(lv)

    if parent_leave_id is None:
        teacher.status = leave_type
        teacher.titular = True
    else:
        teacher.status = TeacherStatus.activo
        teacher.titular = False

    await session.commit()
    return lv


# ============================================================
# CREAR SUSTITUCIÓN
# ============================================================

async def set_substitution(
    session: AsyncSession,
    parent_leave_id: int,
    start_date: date,
    substitute_teacher_id: int,
) -> Leave:
    """
    Crea una baja hija sustituyendo EXACTAMENTE la baja indicada.
    """

    parent_leave = await session.get(Leave, parent_leave_id)
    if not parent_leave or parent_leave.end_date is not None:
        raise HTTPException(404, "La baja seleccionada no está activa.")

    sub_leave = await open_leave(
        session=session,
        teacher_id=substitute_teacher_id,
        start_date=start_date,
        leave_type=TeacherStatus.baja,
        cause="Sustitución",
        category=None,
        parent_leave_id=parent_leave.id
    )

    parent_leave.substitute_teacher_id = substitute_teacher_id
    parent_leave.substitute_start_date = start_date
    parent_leave.substitute_end_date = None

    await session.commit()
    return sub_leave


# ============================================================
# CIERRE EN CASCADA (VUELVE EL TITULAR)
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

    children = await _get_cascade_children(session, leave_id)

    max_child_start = max(
        (ch.start_date for ch in children),
        default=None
    )

    _validate_close_date(
        start_date=leave.start_date,
        end_date=end_date,
        today=date.today(),
        max_child_start=max_child_start
    )

    leave.end_date = end_date
    leave.substitute_teacher_id = None
    leave.substitute_end_date = end_date

    for ch in children:
        ch.end_date = end_date
        ch.substitute_teacher_id = None
        ch.substitute_end_date = end_date

    prof = await session.get(Teacher, leave.teacher_id)
    prof.status = TeacherStatus.activo
    prof.titular = True

    for ch in children:
        p = await session.get(Teacher, ch.teacher_id)
        p.status = TeacherStatus.exprofe
        p.titular = False
        await _delete_cloned_slots(session, p.id)

    await session.commit()
    return leave


# ============================================================
# CIERRE DE SUBÁRBOL (VUELVE UN SUSTITUTO)
# ============================================================

async def close_leave_subtree(
    session: AsyncSession,
    leave_id: int,
    end_date: date
) -> Leave:

    leave = await _get_leave_by_id(session, leave_id)
    if not leave:
        raise HTTPException(404, "La baja no existe.")

    if leave.end_date is not None:
        raise HTTPException(400, "La baja ya está cerrada.")

    children = await _get_cascade_children(session, leave_id)

    max_child_start = max(
        (ch.start_date for ch in children),
        default=None
    )

    _validate_close_date(
        start_date=leave.start_date,
        end_date=end_date,
        today=date.today(),
        max_child_start=max_child_start
    )

    leave.end_date = end_date
    leave.substitute_teacher_id = None
    leave.substitute_end_date = end_date

    for ch in children:
        ch.end_date = end_date
        ch.substitute_teacher_id = None
        ch.substitute_end_date = end_date

    prof = await session.get(Teacher, leave.teacher_id)
    prof.status = TeacherStatus.activo
    prof.titular = False

    for ch in children:
        p = await session.get(Teacher, ch.teacher_id)
        p.status = TeacherStatus.exprofe
        p.titular = False
        await _delete_cloned_slots(session, p.id)

    await session.commit()
    return leave


# ============================================================
# ALIAS DE COMPATIBILIDAD
# ============================================================

async def close_leave(session: AsyncSession, leave_id: int, end_date: date):
    """
    Alias histórico de cierre de baja.
    Mantiene compatibilidad con código antiguo y SIEMPRE realiza
    un cierre en cascada completo (baja raíz).
    """
    return await close_leave_cascade(session, leave_id, end_date)
