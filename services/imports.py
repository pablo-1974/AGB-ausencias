# services/imports.py
from __future__ import annotations
from typing import Optional
import pandas as pd

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models import Teacher, ScheduleSlot, ScheduleType
from utils import require_columns

# --------------------------
# Mapeos
# --------------------------
DAYS = {"Lunes": 0, "Martes": 1, "Miércoles": 2, "Jueves": 3, "Viernes": 4}
HOUR_IDX = {"1ª": 0, "2ª": 1, "3ª": 2, "Recreo": 3, "4ª": 4, "5ª": 5, "6ª": 6}

# Etiquetas válidas para guardias (columna "tipo")
GUARD_LABELS = {"G AULA", "G RECREO PATIO", "G RECREO PASILLO"}


# --------------------------
# Helpers de normalización
# --------------------------
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza cabeceras a minúsculas y sin espacios alrededor.
    No elimina tildes (se esperan tal cual 'día').
    """
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _alias_guards(df: pd.DataFrame) -> pd.DataFrame:
    """
    Import GUARDIAS:
      - Nuevo nombre: 'tipo'
      - Retrocompat.: si viene 'franja horaria' se mapea a 'tipo'
      - 'hora' es opcional; si no está y 'tipo' empieza por 'G RECREO', hora=Recreo (3)
    """
    df = _norm_cols(df)
    if "tipo" not in df.columns and "franja horaria" in df.columns:
        df = df.rename(columns={"franja horaria": "tipo"})
    return df


def _alias_classes(df: pd.DataFrame) -> pd.DataFrame:
    """
    Import CLASES:
      - Nuevo nombre: 'hora'
      - Retrocompat.: si viene 'franja horaria' se mapea a 'hora'
    """
    df = _norm_cols(df)
    if "hora" not in df.columns and "franja horaria" in df.columns:
        df = df.rename(columns={"franja horaria": "hora"})
    return df


# -----------------------------------------
# Profesores
# Excel esperado (tolerante en cabeceras):
#   nombre, email
# -----------------------------------------
async def import_teachers_from_excel(path: str, session: AsyncSession):
    # ✅ LEER SIEMPRE TODO COMO STRING
    df = pd.read_excel(path, dtype=str)

    df = _norm_cols(df)  # normaliza cabeceras
    require_columns(df, ["nombre", "email"])

    imported_list = []   # ✅ YA NO USAMOS cnt

    for _, r in df.iterrows():
        # ------------------------------------
        # NOMBRE / EMAIL (obligatorios)
        # ------------------------------------
        name = (r.get("nombre") or "").strip()
        email = (r.get("email") or "").strip().lower()

        if not name or not email:
            continue

        # ------------------------------------
        # ALIAS (opcional, siempre string)
        # ------------------------------------
        alias_raw = r.get("alias")
        alias = str(alias_raw).strip() if alias_raw else name

        if not alias or alias.lower() in ("", "nan", "none", "null"):
            alias = name

        # ------------------------------------
        # ¿Existe ya por e‑mail?
        # ------------------------------------
        existing = (
            await session.execute(
                select(Teacher).where(Teacher.email == email)
            )
        ).scalar_one_or_none()

        if existing:
            changed = False

            if existing.name != name:
                existing.name = name
                changed = True

            if hasattr(existing, "alias") and existing.alias != alias:
                existing.alias = alias
                changed = True

            if changed:
                imported_list.append({
                    "name": name,
                    "email": email,
                    "alias": alias,
                })

            continue

        # ------------------------------------
        # Crear profesor nuevo
        # ------------------------------------
        data = {"name": name, "email": email, "alias": alias}
        session.add(Teacher(**data))

        imported_list.append({
            "name": name,
            "email": email,
            "alias": alias,
        })

    await session.commit()
    return imported_list     # ✅ ✅ ¡ESTO es lo que necesitaba tu router!
    

# -----------------------------------------
# Guardias (sólo guardias)
# Excel esperado (nuevo): nombre, día, tipo  [+ opcional: hora]
# Retrocompat: 'franja horaria' -> 'tipo' si hace falta
# -----------------------------------------
async def import_guards_from_excel(path: str, session: AsyncSession) -> int:
    df = pd.read_excel(path)
    df = _alias_guards(df)
    require_columns(df, ["nombre", "día", "tipo"])

    cnt = 0
    for _, r in df.iterrows():
        name = str(r["nombre"]).strip()
        day_name = str(r["día"]).strip()
        tipo = str(r["tipo"]).strip()

        # Filtrar sólo guardias válidas
        if tipo not in GUARD_LABELS:
            continue

        day = DAYS.get(day_name)

        # Heurística de hora:
        # - Si 'tipo' empieza por "G RECREO", hora = Recreo (3)
        # - Si no, necesitamos 'hora' (si no hay, omitimos la fila)
        hour = 3 if tipo.startswith("G RECREO") else None
        if "hora" in df.columns and pd.notna(r.get("hora")):
            hour_label = str(r.get("hora")).strip()
            hour = HOUR_IDX.get(hour_label, hour)

        if day is None or hour is None:
            continue

        # Buscar/crear profesor
        teacher = (
            await session.execute(select(Teacher).where(Teacher.name == name))
        ).scalar_one_or_none()
        if not teacher:
            # Si no existe, lo creamos con email ficticio; puedes exigir Excel con email si prefieres.
            teacher = Teacher(name=name, email=f"{name.replace(' ', '_').lower()}@local")
            session.add(teacher)
            await session.flush()  # para obtener id

        # Evitar duplicados exactos (mismo teacher/day/hour/type)
        exists = (
            await session.execute(
                select(ScheduleSlot).where(
                    and_(
                        ScheduleSlot.teacher_id == teacher.id,
                        ScheduleSlot.day_index == day,
                        ScheduleSlot.hour_index == hour,
                        ScheduleSlot.type == ScheduleType.GUARD,
                    )
                )
            )
        ).scalar_one_or_none()

        if exists:
            # actualizar tipo si cambió (por si antes estaba vacío o distinto)
            exists.guard_type = tipo
        else:
            session.add(
                ScheduleSlot(
                    teacher_id=teacher.id,
                    day_index=day,
                    hour_index=hour,
                    type=ScheduleType.GUARD,
                    guard_type=tipo,
                    source="guards_excel",
                )
            )
            cnt += 1

    await session.commit()
    return cnt


# -----------------------------------------
# Horario completo (clases)
# Excel esperado (nuevo): nombre, día, hora, grupo, aula, materia
# Retrocompat: 'franja horaria' -> 'hora' si hace falta
# Nota: Exige que el profesor exista ya (Teacher.name)
# -----------------------------------------
async def import_classes_from_excel(path: str, session: AsyncSession) -> int:
    df = pd.read_excel(path)
    df = _alias_classes(df)  # mapea 'franja horaria' -> 'hora' si hace falta
    require_columns(df, ["nombre", "día", "hora", "grupo", "aula", "materia"])

    cnt = 0
    for _, r in df.iterrows():
        name = str(r["nombre"]).strip()
        day = DAYS.get(str(r["día"]).strip())
        hour_label = str(r["hora"]).strip()
        hour = HOUR_IDX.get(hour_label)

        group = str(r["grupo"]).strip()
        room = str(r["aula"]).strip()
        subject = str(r["materia"]).strip()

        if day is None or hour is None:
            # Día u hora no reconocidos -> omitimos fila
            continue

        # Buscar profesor por nombre
        teacher = (
            await session.execute(select(Teacher).where(Teacher.name == name))
        ).scalar_one_or_none()

        if not teacher:
            # Crear profesor si no existe (igual que en guardias), con email ficticio predecible
            email_fallback = f"{name.replace(' ', '_').lower()}@local"
            # Si tu modelo Teacher tiene 'alias' NOT NULL, puedes usar alias=name
            data = {"name": name, "email": email_fallback}
            if hasattr(Teacher, "alias"):
                data["alias"] = name
            teacher = Teacher(**data)
            session.add(teacher)
            await session.flush()  # para obtener teacher.id

        session.add(
            ScheduleSlot(
                teacher_id=teacher.id,
                day_index=day,
                hour_index=hour,
                type=ScheduleType.CLASS,
                group=group,
                room=room,
                subject=subject,
                source="classes_excel",
            )
        )
        cnt += 1

    await session.commit()
    return cnt
