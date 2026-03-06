# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Leave, Teacher


async def open_leave(session: AsyncSession, teacher_id: int, start_date: date) -> Leave:
    """
    Inicia una baja para un profesor.
    Si hay otra baja solapada sin cerrar, la reutiliza/ajusta.
    """
    # Buscar baja activa o solapada
    q = select(Leave).where(
        and_(Leave.teacher_id == teacher_id,
             or_(Leave.end_date == None, Leave.end_date >= start_date))
    )
    existing = (await session.execute(q)).scalar_one_or_none()
    if existing:
        # Si ya existe activa, no duplicamos
        if existing.start_date > start_date:
            existing.start_date = start_date
        await session.commit()
        await session.refresh(existing)
        return existing

    lv = Leave(teacher_id=teacher_id, start_date=start_date, end_date=None, substitute_teacher_id=None)
    session.add(lv)
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
    """
    q = select(Leave).where(
        and_(Leave.teacher_id == teacher_id,
             Leave.start_date <= start_date,
             or_(Leave.end_date == None, Leave.end_date >= start_date))
    )
    lv = (await session.execute(q)).scalar_one_or_none()
    if not lv:
        # si no hay baja, la creamos
        lv = await open_leave(session, teacher_id, start_date)

    if substitute_teacher_id is None and substitute_name:
        # crear teacher nuevo si no existe
        exists = (await session.execute(select(Teacher).where(Teacher.name == substitute_name.strip()))).scalar_one_or_none()
        if not exists:
            exists = Teacher(name=substitute_name.strip(), email=(substitute_email or f"{substitute_name.replace(' ','').lower()}@local"))
            session.add(exists)
            await session.flush()
        substitute_teacher_id = exists.id

    lv.substitute_teacher_id = substitute_teacher_id
    await session.commit()
    await session.refresh(lv)
    return lv


async def close_leave(session: AsyncSession, teacher_id: int, end_date: date) -> Leave:
    """
    Cierra la baja: fija fecha fin y 'desactiva' (active=False) el sustituto si había.
    (No se borra de la base de datos, sólo se mantiene histórico)
    """
    q = select(Leave).where(and_(Leave.teacher_id == teacher_id, Leave.end_date == None))
    lv = (await session.execute(q)).scalar_one_or_none()
    if not lv:
        raise ValueError("No hay baja abierta para este profesor")

    lv.end_date = end_date

    # El sustituto deja de ser profesor del centro, pero no se borra
    if lv.substitute_teacher_id:
        sub = await session.get(Teacher, lv.substitute_teacher_id)
        if sub:
            sub.active = False

    await session.commit()
    await session.refresh(lv)
    return lv
