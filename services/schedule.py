# services/schedule.py
from __future__ import annotations
from typing import List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from models import ScheduleSlot, ScheduleType, Teacher


# ---------------------------------
# Consulta de slot principal (prioridad: CLASE > GUARDIA)
# ---------------------------------
async def get_teacher_slot(session: AsyncSession, teacher_id: int, day_idx: int, hour_idx: int) -> Optional[ScheduleSlot]:
    q = select(ScheduleSlot).where(
        and_(ScheduleSlot.teacher_id == teacher_id,
             ScheduleSlot.day_index == day_idx,
             ScheduleSlot.hour_index == hour_idx)
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
    absent_teacher_ids: Set[int]
) -> List[str]:
    q = select(ScheduleSlot, Teacher).join(Teacher, Teacher.id == ScheduleSlot.teacher_id).where(
        and_(ScheduleSlot.day_index == day_idx,
             ScheduleSlot.hour_index == hour_idx,
             ScheduleSlot.type == ScheduleType.GUARD)
    )
    res = (await session.execute(q)).all()
    # Excluir ausentes
    names = [t.name for slot, t in res if t.id not in absent_teacher_ids]
    return sorted(names)


# ---------------------------------
# Ver horario de un profesor (todos los slots)
# ---------------------------------
async def get_teacher_schedule(session: AsyncSession, teacher_id: int) -> List[ScheduleSlot]:
    q = select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id).order_by(
        ScheduleSlot.day_index.asc(), ScheduleSlot.hour_index.asc(), ScheduleSlot.type.desc()
    )
    return (await session.execute(q)).scalars().all()


# ---------------------------------
# Añadir guardia manual
# ---------------------------------
async def add_guard_slot(
    session: AsyncSession, teacher_id: int, day_idx: int, hour_idx: int, guard_type: str
) -> ScheduleSlot:
    slot = ScheduleSlot(
        teacher_id=teacher_id, day_index=day_idx, hour_index=hour_idx,
        type=ScheduleType.GUARD, guard_type=guard_type, source="manual"
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
