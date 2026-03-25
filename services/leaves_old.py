# services/leaves.py
from __future__ import annotations
from datetime import date
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import Leave, Teacher, TeacherStatus, ScheduleSlot
from fastapi import HTTPException


async def open_leave(
    session: AsyncSession,
    teacher_id: int,
    start_date: date,
    leave_type: TeacherStatus,   # TeacherStatus.baja o TeacherStatus.excedencia
    cause: str,                  
    category: str,               # ⬅⬅⬅ NUEVO
) -> Leave:
    """
    Inicia una baja o excedencia.
    - La BAJA requiere categoría A–L obligatoria.
    - La EXCEDENCIA no lleva categoría.
    - No duplicamos bajas solapadas.
    """

    # -------- Validación tipo --------
    if leave_type not in (TeacherStatus.baja, TeacherStatus.excedencia):
        raise HTTPException(status_code=400, detail="Tipo de baja no válido (usa 'baja' o 'excedencia').")

    if not isinstance(start_date, date):
        raise HTTPException(status_code=400, detail="Fecha de inicio no válida.")

    cause = (cause or "").strip()
    if not cause:
        raise HTTPException(status_code=400, detail="La causa es obligatoria.")

    # -------- Cargar profesor --------
    teacher = (await session.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )).scalar_one_or_none()

    if not teacher:
        raise HTTPException(status_code=404, detail="Profesor no encontrado.")

    if teacher.status != TeacherStatus.activo:
        raise HTTPException(status_code=400, detail="Solo se puede iniciar baja/excedencia a un profesor en 'activo'.")

    # -------- Validación categoría --------
    if leave_type == TeacherStatus.baja:
        # Las BAJAS necesitan categoría A–L
        if category not in list("ABCDEFGHIJKL"):
            raise HTTPException(status_code=400, detail="Categoría inválida. Debe ser A–L.")
    else:
        # Las EXCEDENCIAS NO llevan categoría
        category = None

    # --------------------------------------------------
    # Buscar baja activa o solapada existente
    # --------------------------------------------------
    q = select(Leave).where(
        and_(
            Leave.teacher_id == teacher_id,
            or_(Leave.end_date == None, Leave.end_date >= start_date)
        )
    )
    existing = (await session.execute(q)).scalar_one_or_none()

    if existing:
        # Ya había una baja o excedencia activa o solapada
        if existing.start_date > start_date:
            existing.start_date = start_date

        if not getattr(existing, "cause", None):
            existing.cause = cause

        # Si es baja → categoría se actualiza
        if leave_type == TeacherStatus.baja:
            existing.category = category
        else:
            existing.category = None

        teacher.status = leave_type

        await session.commit()
        await session.refresh(existing)
        return existing

    # --------------------------------------------------
    # Crear BAJA nueva
    # --------------------------------------------------
    lv = Leave(
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=None,
        cause=cause,
        category=category,                # ⬅⬅⬅ NUEVO
        substitute_teacher_id=None,
        substitute_start_date=None,
        substitute_end_date=None,
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
    lv.substitute_start_date = start_date
    await session.commit()
    await session.refresh(lv)
    return lv


async def close_leave(session: AsyncSession, teacher_id: int, end_date: date) -> Leave:
    """
    Cierra la baja: fija fecha fin, devuelve al profesor a 'activo' y
    revierte la sustitución si existía:
      - El sustituto pasa a 'exprofe'
      - Se eliminan del sustituto los slots clonados (source='substitution:{teacher_id}')
      - El sustituido conserva su horario (no lo tocamos)
    """
    if not isinstance(end_date, date):
        raise HTTPException(status_code=400, detail="Fecha de fin no válida.")

    # 1) Baja abierta del profesor (sustituido)
    q = select(Leave).where(and_(Leave.teacher_id == teacher_id, Leave.end_date == None))
    lv = (await session.execute(q)).scalar_one_or_none()
    if not lv:
        raise HTTPException(status_code=404, detail="No hay baja abierta para este profesor.")

    # 2) Cerrar baja
    lv.end_date = end_date

    # 3) Devolver status del sustituido a 'activo'
    teacher = (await session.execute(
        select(Teacher).where(Teacher.id == teacher_id)
    )).scalar_one_or_none()
    if teacher:
        teacher.status = TeacherStatus.activo

    # 4) Si hubo sustituto: pasarlo a 'exprofe' y limpiar sus slots clonados
    if lv.substitute_teacher_id:
        sub = (await session.execute(
            select(Teacher).where(Teacher.id == lv.substitute_teacher_id)
        )).scalar_one_or_none()

        if sub:
            # Status del sustituto -> exprofe
            sub.status = TeacherStatus.exprofe

            # Eliminar solo los slots que clonamos al iniciar sustitución
            # (source="substitution:{teacher_id_sustituido}")
            flag = f"substitution:{teacher_id}"
            sub_slots = (await session.execute(
                select(ScheduleSlot).where(
                    and_(
                        ScheduleSlot.teacher_id == sub.id,
                        ScheduleSlot.source == flag
                    )
                )
            )).scalars().all()

            for s in sub_slots:
                await session.delete(s)


        # Fin de sustitución = fecha de cierre de la baja
        lv.substitute_end_date = end_date
    
    # 5) Persistir cambios
    await session.commit()
    await session.refresh(lv)
    return lv


