# services/schedule.py
from __future__ import annotations
from typing import List, Optional, Set, Tuple
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from models import ScheduleSlot, ScheduleType, Teacher, Leave


# ---------------------------------
# Consulta de slot principal (prioridad: CLASE > GUARDIA)
# ---------------------------------
async def get_teacher_slot(session: AsyncSession, teacher_id: int, day_idx: int, hour_idx: int) -> Optional[ScheduleSlot]:
    q = select(ScheduleSlot).where(
        and_(
            ScheduleSlot.teacher_id == teacher_id,
            ScheduleSlot.day_index == day_idx,
            ScheduleSlot.hour_index == hour_idx,
        )
    )
    rows = (await session.execute(q)).scalars().all()
    cls = next((r for r in rows if r.type == ScheduleType.CLASS), None)
    if cls:
        return cls
    grd = next((r for r in rows if r.type == ScheduleType.GUARD), None)
    return grd


# ---------------------------------
# Profes en guardia en un tramo (excluye ausentes)
# ---------------------------------
async def list_teachers_on_guard(
    session: AsyncSession,
    day_idx: int,
    hour_idx: int,
    the_date: date,
    absent_teacher_ids: Set[int],
) -> List[str]:

    q = select(ScheduleSlot, Teacher).join(
        Teacher, Teacher.id == ScheduleSlot.teacher_id
    ).where(
        and_(
            ScheduleSlot.day_index == day_idx,
            ScheduleSlot.hour_index == hour_idx,
            ScheduleSlot.type == ScheduleType.GUARD,
        )
    )

    res = (await session.execute(q)).all()
    valid_ids = []

    for slot, teacher in res:
        # 1️⃣ Excluir ausentes
        if teacher.id in absent_teacher_ids:
            continue

        # 2️⃣ Excluir sustitutos que aún no han empezado
        future_sub = await session.execute(
            select(Leave.id).where(
                and_(
                    Leave.teacher_id == teacher.id,
                    Leave.parent_leave_id.is_not(None),
                    Leave.start_date > the_date,
                )
            )
        )
        if future_sub.first():
            continue

        valid_ids.append(teacher.id)

    return sorted(valid_ids)


# ---------------------------------
# Ver horario de un profesor (todos los slots)
# ---------------------------------
async def get_teacher_schedule(session: AsyncSession, teacher_id: int) -> List[ScheduleSlot]:
    q = select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id).order_by(
        ScheduleSlot.day_index.asc(),
        ScheduleSlot.hour_index.asc(),
        ScheduleSlot.type.desc(),
    )
    return (await session.execute(q)).scalars().all()


# ---------------------------------
# Añadir guardia manual
# ---------------------------------
async def add_guard_slot(
    session: AsyncSession, teacher_id: int, day_idx: int, hour_idx: int, guard_type: str
) -> ScheduleSlot:
    slot = ScheduleSlot(
        teacher_id=teacher_id,
        day_index=day_idx,
        hour_index=hour_idx,
        type=ScheduleType.GUARD,
        guard_type=guard_type,
        source="manual",
    )
    session.add(slot)
    await session.commit()
    await session.refresh(slot)
    return slot


# ---------------------------------
# Quitar un slot (por id)
# ---------------------------------
async def delete_slot(session: AsyncSession, slot_id: int) -> None:
    s = await session.get(ScheduleSlot, slot_id)
    if not s:
        return
    await session.delete(s)
    await session.commit()


# ---------------------------------
# Clonar horario de un profesor (herencia por sustitución)
# ---------------------------------
async def clone_teacher_schedule(
    session: AsyncSession,
    source_teacher_id: int,
    target_teacher_id: int,
    effective_from: date | None = None,
    replace_existing: bool = True,
) -> int:
    """
    Copia el horario (ScheduleSlot) del profesor 'source' al profesor 'target'.

    - Si 'replace_existing' es True (por defecto), elimina en el target los slots
      que coincidan en (day_index, hour_index) con los del source antes de clonar,
      para evitar duplicidades/choques.
    - 'effective_from' se incluye por compatibilidad futura. Tu modelo actual no
      gestiona vigencia por fecha, por lo que se ignora para el filtrado.
    - Devuelve el número de slots creados en el target.

    Reglas:
      * Copia tanto CLASS como GUARD.
      * Mantiene group/room/subject en CLASS y guard_type en GUARD.
      * Marca 'source' = "substitution:{source_teacher_id}" para trazar el origen.
    """
    if source_teacher_id == target_teacher_id:
        return 0

    # 1) Leer slots del profesor origen
    src_slots: List[ScheduleSlot] = (
        await session.execute(select(ScheduleSlot).where(ScheduleSlot.teacher_id == source_teacher_id))
    ).scalars().all()

    if not src_slots:
        return 0

    # 2) Conjunto de pares (día, hora) presentes en el origen
    pairs: Set[Tuple[int, int]] = {(s.day_index, s.hour_index) for s in src_slots}

    # 3) Si hay que reemplazar, limpiar en el target las franjas coincidentes
    if replace_existing and pairs:
        for d, h in pairs:
            target_slots_same_pair: List[ScheduleSlot] = (
                await session.execute(
                    select(ScheduleSlot).where(
                        and_(
                            ScheduleSlot.teacher_id == target_teacher_id,
                            ScheduleSlot.day_index == d,
                            ScheduleSlot.hour_index == h,
                        )
                    )
                )
            ).scalars().all()
            for ts in target_slots_same_pair:
                await session.delete(ts)
        # (No commit aún; lo hacemos al final tras insertar)

    # 4) Clonar todos los slots del source al target
    created = 0
    for s in src_slots:
        clone = ScheduleSlot(
            teacher_id=target_teacher_id,
            day_index=s.day_index,
            hour_index=s.hour_index,
            type=s.type,
            # Campos dependientes del tipo:
            guard_type=s.guard_type if s.type == ScheduleType.GUARD else None,
            group=s.group if s.type == ScheduleType.CLASS else None,
            room=s.room if s.type == ScheduleType.CLASS else None,
            subject=s.subject if s.type == ScheduleType.CLASS else None,
            # Marcar el origen para trazabilidad
            source=f"substitution:{source_teacher_id}",
        )
        session.add(clone)
        created += 1

    await session.commit()
    return created

