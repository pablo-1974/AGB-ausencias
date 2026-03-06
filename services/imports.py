# services/imports.py
from __future__ import annotations
from typing import Optional
import pandas as pd

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models import Teacher, ScheduleSlot, ScheduleType
from utils import require_columns

# Mapeos
DAYS = {"Lunes": 0, "Martes": 1, "Miércoles": 2, "Jueves": 3, "Viernes": 4}
HOUR_IDX = {"1ª": 0, "2ª": 1, "3ª": 2, "Recreo": 3, "4ª": 4, "5ª": 5, "6ª": 6}

GUARD_LABELS = {"G AULA", "G RECREO PATIO", "G RECREO PASILLO"}


# -----------------------------------------
# Profesores
# -----------------------------------------
async def import_teachers_from_excel(path: str, session: AsyncSession) -> int:
    df = pd.read_excel(path)
    require_columns(df, ["nombre", "email"])
    cnt = 0
    for _, r in df.iterrows():
        name = str(r["nombre"]).strip()
        email = str(r["email"]).strip().lower()
        if not name or not email:
            continue
        existing = (await session.execute(select(Teacher).where(Teacher.email == email))).scalar_one_or_none()
        if existing:
            # opcional: actualizar nombre si cambió
            if existing.name != name:
                existing.name = name
            continue
        session.add(Teacher(name=name, email=email))
        cnt += 1
    await session.commit()
    return cnt


# -----------------------------------------
# Guardias (sólo guardias, como pediste)
# Excel esperado: nombre, día, franja horaria
# -----------------------------------------
async def import_guards_from_excel(path: str, session: AsyncSession) -> int:
    df = pd.read_excel(path)
    # Normalizar columnas
    df.columns = [c.strip().lower() for c in df.columns]
    require_columns(df, ["nombre", "día", "franja horaria"])

    cnt = 0
    for _, r in df.iterrows():
        name = str(r["nombre"]).strip()
        day_name = str(r["día"]).strip()
        franja = str(r["franja horaria"]).strip()

        if franja not in GUARD_LABELS:
            # Saltamos todo lo que no sea guardia (por requerimiento)
            continue

        day = DAYS.get(day_name)
        # Heurística de índice de hora:
        # Si dice "G RECREO ..." colocamos en RECREO (3); si no, exige columna "hora" si existiese
        hour = 3 if franja.startswith("G RECREO") else None

        # si hay columna "hora" exacta, úsala:
        if "hora" in df.columns and not pd.isna(r.get("hora")):
            hour_label = str(r["hora"]).strip()
            hour = HOUR_IDX.get(hour_label, hour)

        if day is None or hour is None:
            continue

        teacher = (await session.execute(select(Teacher).where(Teacher.name == name))).scalar_one_or_none()
        if not teacher:
            # Si no existe, lo creamos con email ficticio; puedes exigir Excel con email si prefieres
            teacher = Teacher(name=name, email=f"{name.replace(' ', '_').lower()}@local")
            session.add(teacher)
            await session.flush()

        # Evitar duplicados exactos
        exists = (await session.execute(
            select(ScheduleSlot).where(
                and_(ScheduleSlot.teacher_id == teacher.id,
                     ScheduleSlot.day_index == day,
                     ScheduleSlot.hour_index == hour,
                     ScheduleSlot.type == ScheduleType.GUARD)
            )
        )).scalar_one_or_none()
        if exists:
            exists.guard_type = franja  # actualiza tipo si cambió
        else:
            session.add(ScheduleSlot(
                teacher_id=teacher.id,
                day_index=day,
                hour_index=hour,
                type=ScheduleType.GUARD,
                guard_type=franja,
                source="guards_excel"
            ))
            cnt += 1

    await session.commit()
    return cnt


# -----------------------------------------
# (Opcional) Importar horario completo (clases)
# Excel esperado: nombre, día, franja, grupo, aula, materia
# -----------------------------------------
async def import_classes_from_excel(path: str, session: AsyncSession) -> int:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower() for c in df.columns]
    require_columns(df, ["nombre", "día", "franja horaria", "grupo", "aula", "materia"])

    cnt = 0
    for _, r in df.iterrows():
        name = str(r["nombre"]).strip()
        day = DAYS.get(str(r["día"]).strip())
        hour = HOUR_IDX.get(str(r["franja horaria"]).strip())
        group = str(r["grupo"]).strip()
        room = str(r["aula"]).strip()
        subject = str(r["materia"]).strip()

        if day is None or hour is None:
            continue

        teacher = (await session.execute(select(Teacher).where(Teacher.name == name))).scalar_one_or_none()
        if not teacher:
            continue  # exigimos que el profesor exista ya

        session.add(ScheduleSlot(
            teacher_id=teacher.id, day_index=day, hour_index=hour,
            type=ScheduleType.CLASS, group=group, room=room, subject=subject, source="classes_excel"
        ))
        cnt += 1

    await session.commit()
    return cnt
