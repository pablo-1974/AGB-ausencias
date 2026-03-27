# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Leave, Teacher, TeacherStatus, ScheduleSlot
from fastapi import HTTPException


# ---------------------------------------------------------
# Helper: obtener la baja ACTIVA de un profesor
# ---------------------------------------------------------
async def _get_open_leave(session: AsyncSession, teacher_id: int) -> Optional[Leave]:
    return await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None)
            )
        )
    )


# ---------------------------------------------------------
# Helper: obtener sustituto directo del profesor
# ---------------------------------------------------------
async def _get_direct_substitute(session: AsyncSession, teacher_id: int) -> Optional[int]:
    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        return None
    return lv.substitute_teacher_id


# ---------------------------------------------------------
# Helper: reconstruir cadena completa de sustituciones
# Devuelve: [sub1, sub2, sub3, ...] (sin incluir al titular)
# ---------------------------------------------------------
async def get_substitution_chain(session: AsyncSession, teacher_id: int) -> List[int]:
    chain = []
    cur = teacher_id

    while True:
        sub_id = await _get_direct_substitute(session, cur)
        if not sub_id:
            break
        chain.append(sub_id)
        cur = sub_id

    return chain


# ---------------------------------------------------------
# Abrir una baja (baja o excedencia)
# ---------------------------------------------------------
async def open_leave(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    leave_type: TeacherStatus,   # baja o excedencia
    cause: str,
    category: Optional[str] = None,
) -> Leave:

    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(400, "Tipo de baja no válido.")

    if not isinstance(start_date, date):
        raise HTTPException(400, "Fecha de inicio no válida.")

    teacher = await session.scalar(select(Teacher).where(Teacher.id == teacher_id))
    if not teacher:
        raise HTTPException(404, "Profesor no encontrado.")

    if teacher.status != TeacherStatus.activo:
        raise HTTPException(400, "Solo se puede iniciar baja a un profesor activo.")

    if leave_type == TeacherStatus.baja:
        # Si se pasó categoría, validarla
        if category:
            if category not in list("ABCDEFGHIJKL"):
                raise HTTPException(status_code=400, detail="Categoría inválida. Debe ser A–L.")
        else:
            # Permitir iniciar baja sin catalogación
            category = None
    else:
        # Excedencias no llevan categoría
        category = None

    # Buscar baja existente solapada
    q = select(Leave).where(
        and_(
            Leave.teacher_id == teacher_id,
            or_(Leave.end_date == None, Leave.end_date >= start_date)
        )
    )
    existing = await session.scalar(q)

    if existing:
        # Actualizar baja existente
        if existing.start_date > start_date:
            existing.start_date = start_date

        existing.cause = cause or existing.cause
        existing.category = category
        teacher.status = leave_type

        await session.commit()
        return existing

    # Crear nueva baja
    lv = Leave(
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category,
    )

    session.add(lv)
    teacher.status = leave_type

    await session.commit()
    return lv


# ---------------------------------------------------------
# Asignar sustituto (primer nivel o niveles posteriores)
# ---------------------------------------------------------
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
        # Crear baja automática si no existe
        lv = await open_leave(
            session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=TeacherStatus.baja,
            cause="Alta automática de sustitución"
        )

    # Asignar sustituto directo
    lv.substitute_teacher_id = substitute_teacher_id
    lv.substitute_start_date = start_date
    lv.substitute_end_date = None

    # Activar sustituto
    sub = await session.get(Teacher, substitute_teacher_id)
    if sub:
        sub.status = TeacherStatus.activo
        sub.titular = False

    await session.commit()
    return lv


# ---------------------------------------------------------
# Finalizar solo la sustitución (sin cerrar la baja)
# ---------------------------------------------------------
async def end_substitution(
    session: AsyncSession,
    teacher_id: int,
    end_date: date
):
    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        raise HTTPException(404, "No hay baja activa para este profesor.")

    sub_id = lv.substitute_teacher_id
    if not sub_id:
        return  # no hay sustituto

    # Cerrar sustitución
    lv.substitute_end_date = end_date
    lv.substitute_teacher_id = None

    # Degradar sustituto
    sub = await session.get(Teacher, sub_id)
    if sub:
        sub.status = TeacherStatus.exprofe
        sub.titular = False

        # Borrar horario clonado
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


# ---------------------------------------------------------
# Finalizar la BAJA COMPLETA (cierre en cascada)
# ---------------------------------------------------------
async def close_leave_cascade(
    session: AsyncSession,
    teacher_id: int,
    end_date: date
) -> Leave:

    if not isinstance(end_date, date):
        raise HTTPException(400, "Fecha de fin no válida.")

    # 1) Cerrar la baja del titular
    lv = await _get_open_leave(session, teacher_id)
    if not lv:
        raise HTTPException(404, "No hay baja abierta para este profesor.")

    lv.end_date = end_date

    titular = await session.get(Teacher, teacher_id)
    titular.status = TeacherStatus.activo
    titular.titular = True

    # 2) Obtener cadena completa de sustitutos
    chain = await get_substitution_chain(session, teacher_id)

    cur_sustituido = teacher_id

    for sub_id in chain:
        sub = await session.get(Teacher, sub_id)
        if sub:
            sub.status = TeacherStatus.exprofe
            sub.titular = False

            flag = f"substitution:{cur_sustituido}"
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

        # Cerrar relación de sustitución
        lv_sust = await _get_open_leave(session, cur_sustituido)
        if lv_sust:
            lv_sust.substitute_end_date = end_date
            lv_sust.substitute_teacher_id = None

        cur_sustituido = sub_id

    await session.commit()
    await session.refresh(lv)
    return lv


# ---------------------------------------------------------
# ALIAS: compatibilidad con router antiguo
# ---------------------------------------------------------
async def close_leave(session, teacher_id: int, end_date: date):
    return await close_leave_cascade(session, teacher_id, end_date)
