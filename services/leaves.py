# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Leave, Teacher, TeacherStatus
from fastapi import HTTPException


async def open_leave(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    leave_type: TeacherStatus,   # TeacherStatus.baja o TeacherStatus.excedencia
    cause: str,                  # obligatorio
) -> Leave:
    """
    Inicia una baja/excedencia para un profesor.
    - Requiere que el profesor esté 'activo'.
    - Si existe una baja abierta o solapada, ajusta el inicio (no duplica).
    - Cambia el status del profesor al tipo elegido.
    """
    # Validaciones de tipo
    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(status_code=400, detail="Tipo de baja no válido (usa 'baja' o 'excedencia').")
    if not isinstance(start_date, date):
        raise HTTPException(status_code=400, detail="Fecha de inicio no válida.")
    cause = (cause or "").strip()
    if not cause:
        raise HTTPException(status_code=400, detail="La causa es obligatoria.")

    # Cargar profesor y validar estado actual
    teacher = (await session.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )).scalar_one_or_none()
    if not teacher:
        raise HTTPException(status_code=404, detail="Profesor no encontrado.")
    if teacher.status != TeacherStatus.activo:
        raise HTTPException(status_code=400, detail="Solo se puede iniciar baja/excedencia a un profesor en 'activo'.")

    # Buscar baja activa o solapada
    q = select(Leave).where(
        and_(
            Leave.teacher_id == teacher_id,
            or_(Leave.end_date == None, Leave.end_date >= start_date)
        )
    )
    existing = (await session.execute(q)).scalar_one_or_none()
    if existing:
        # Si ya existe activa o solapada, no creamos otra.
        # Ajustamos fecha de inicio si la nueva es anterior.
        if existing.start_date > start_date:
            existing.start_date = start_date
        # Aseguramos que cause esté informada (si antes no se guardaba)
        if not getattr(existing, "cause", None):
            existing.cause = cause
        # Cambiamos el status del profesor al tipo actual elegido
        teacher.status = leave_type
        await session.commit()
        await session.refresh(existing)
        return existing

    # Crear Leave nueva con cause
    lv = Leave(
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=None,
        cause=cause,  # <-- requiere columna en BD
        substitute_teacher_id=None
    )
    session.add(lv)

    # Cambiar estado del profesor
    teacher.status = leave_type

    await session.commit()
    await session.refresh(lv)
    return lv


async def set_substitution(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    substitute_teacher_id: Optional[int] = None,
    substitute_name: Optional[str] = None,
    substitute_email: Optional[str] = None,
) -> Leave:
    """
    Asigna sustituto a una baja. Si no existe el sustituto y se da nombre, lo crea.
    (No modificamos estados de nadie en esta función todavía; reglas por definir.)
    """
    q = select(Leave).where(
        and_(
            Leave.teacher_id == teacher_id,
            Leave.start_date <= start_date,
            or_(Leave.end_date == None, Leave.end_date >= start_date)
        )
    )
    lv = (await session.execute(q)).scalar_one_or_none()
    if not lv:
        # si no hay baja, la creamos con tipo 'baja' por defecto y causa técnica
        # (alternativamente, podrías exigir que exista una baja).
        lv = await open_leave(
            session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=TeacherStatus.baja,
            cause="Alta de sustitución (auto)"
        )

    if substitute_teacher_id is None and substitute_name:
        # crear teacher nuevo si no existe
        exists = (await session.execute(
            select(Teacher).where(Teacher.name == substitute_name.strip())
        )).scalar_one_or_none()
        if not exists:
            exists = Teacher(
                name=substitute_name.strip(),
                email=(substitute_email or f"{substitute_name.replace(' ','').lower()}@local")
            )
            session.add(exists)
            await session.flush()
        substitute_teacher_id = exists.id

    lv.substitute_teacher_id = substitute_teacher_id
    await session.commit()
    await session.refresh(lv)
    return lv


async def close_leave(session: AsyncSession, teacher_id: int, end_date: date) -> Leave:
    """
    Cierra la baja: fija fecha fin y devuelve al profesor a 'activo'.
    (No se borra histórico ni se tocan estados del sustituto en esta fase.)
    """
    if not isinstance(end_date, date):
        raise HTTPException(status_code=400, detail="Fecha de fin no válida.")

    q = select(Leave).where(and_(Leave.teacher_id == teacher_id, Leave.end_date == None))
    lv = (await session.execute(q)).scalar_one_or_none()
    if not lv:
        raise HTTPException(status_code=404, detail="No hay baja abierta para este profesor.")

    # Fin de baja
    lv.end_date = end_date

    # Devolver status del profesor a 'activo'
    teacher = (await session.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )).scalar_one_or_none()
    if teacher:
        teacher.status = TeacherStatus.activo

    # Ya no tocamos 'active' del sustituto (ese campo no existe y las reglas están por definir)
    await session.commit()
    await session.refresh(lv)
    return lv

