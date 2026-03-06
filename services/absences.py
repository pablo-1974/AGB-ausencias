# services/absences.py
from __future__ import annotations
from typing import List, Optional, Dict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from models import Absence, Teacher
from utils import hours_list_to_mask, mask_to_hour_list


# -----------------------------
# Crear / editar / borrar
# -----------------------------
async def create_absence(
    session: AsyncSession,
    teacher_id: int,
    on_date: date,
    hours: List[int],
    all_selected: bool = False,
    note: Optional[str] = None,
) -> Absence:
    """
    Crea ausencia para un profesor en una fecha concreta.
    Si ya existía ausencia en esa fecha, suma horas (OR bitmask).
    """
    mask = hours_list_to_mask(hours, all_selected=all_selected)

    q = select(Absence).where(and_(Absence.teacher_id == teacher_id, Absence.date == on_date))
    current = (await session.execute(q)).scalar_one_or_none()
    if current:
        current.hours_mask |= mask
        if note:
            current.note = (current.note or "") + ((" | " + note) if current.note else note)
        await session.commit()
        await session.refresh(current)
        return current

    a = Absence(teacher_id=teacher_id, date=on_date, hours_mask=mask, note=note)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def update_absence(
    session: AsyncSession,
    absence_id: int,
    hours: Optional[List[int]] = None,
    all_selected: Optional[bool] = None,
    note: Optional[str] = None,
    category: Optional[str] = None,
) -> Absence:
    a = await session.get(Absence, absence_id)
    if not a:
        raise ValueError("Ausencia no encontrada")

    if hours is not None:
        a.hours_mask = hours_list_to_mask(hours, all_selected=bool(all_selected))
    if note is not None:
        a.note = note
    if category is not None:
        a.category = category

    await session.commit()
    await session.refresh(a)
    return a


async def delete_absence(session: AsyncSession, absence_id: int) -> None:
    a = await session.get(Absence, absence_id)
    if not a:
        return
    await session.delete(a)
    await session.commit()


# -----------------------------
# Catalogación
# -----------------------------
async def categorize_absence(session: AsyncSession, absence_id: int, code: str) -> Absence:
    a = await session.get(Absence, absence_id)
    if not a:
        raise ValueError("Ausencia no encontrada")
    a.category = code
    await session.commit()
    await session.refresh(a)
    return a


# -----------------------------
# Listados
# -----------------------------
async def list_absences_by_date(session: AsyncSession, on_date: date) -> List[Absence]:
    res = await session.execute(select(Absence).where(Absence.date == on_date))
    return res.scalars().all()


async def list_absences_in_range(session: AsyncSession, date_from: date, date_to: date) -> List[Absence]:
    res = await session.execute(
        select(Absence).where(and_(Absence.date >= date_from, Absence.date <= date_to))
    )
    return res.scalars().all()


async def count_uncategorized_in_range(session: AsyncSession, date_from: date, date_to: date) -> int:
    res = await session.execute(
        select(func.count()).select_from(Absence).where(
            and_(Absence.date >= date_from, Absence.date <= date_to, Absence.category.is_(None))
        )
    )
    return res.scalar_one()
