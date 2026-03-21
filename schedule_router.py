# ======================================================
# schedule_router.py — Gestión y edición de horarios
# ======================================================
# Vista de horario, edición y exportación a PDF.
# Todas las plantillas pasan por el contexto global ctx()
# para unificar fecha/hora, cabecera, usuario y datos comunes.
# ======================================================

from __future__ import annotations

import tempfile
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from starlette.responses import RedirectResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models import Teacher, ScheduleSlot, ScheduleType, User
from config import settings
from services.pdf_schedule import generate_schedule_pdf

# Usuario autenticado
from app import load_user_dep

# Ordenación español
from utils import normalize_name

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter()


# ======================================================
# Helpers de plantillas
# ======================================================
def _templates(request: Request):
    """Accede al motor de plantillas (Jinja2)."""
    return request.app.state.templates


# ======================================================
# GET /schedule/view — selector de profesor
# ======================================================
@router.get("/schedule/view")
async def schedule_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", 303)

    teachers = (await session.execute(select(Teacher))).scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "schedule_view.html",
        ctx(request, user, teachers=teachers, selected_id=None, schedule=None),
    )


# ======================================================
# POST /schedule/view — mostrar horario del profesor
# ======================================================
@router.post("/schedule/view")
async def schedule_view_post(
    request: Request,
    teacher_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", 303)

    teachers = (await session.execute(select(Teacher))).scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    slots = (
        await session.execute(
            select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id)
        )
    ).scalars().all()

    # Matriz 7x5 de franjas y días
    tabla = [[None for _ in range(5)] for _ in range(7)]
    for s in slots:
        if 0 <= s.hour_index <= 6 and 0 <= s.day_index <= 4:
            tabla[s.hour_index][s.day_index] = s

    return _templates(request).TemplateResponse(
        "schedule_view.html",
        ctx(request, user, teachers=teachers, selected_id=teacher_id, schedule=tabla),
    )


# ======================================================
# GET /schedule/edit/{teacher_id} — edición del horario
# ======================================================
@router.get("/schedule/edit/{teacher_id}")
async def schedule_edit(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", 303)

    teacher = await session.get(Teacher, teacher_id)
    if not teacher:
        return RedirectResponse("/schedule/view", 303)

    slots = (
        await session.execute(
            select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id)
        )
    ).scalars().all()

    tabla = [[None for _ in range(5)] for _ in range(7)]
    for s in slots:
        if 0 <= s.hour_index <= 6 and 0 <= s.day_index <= 4:
            tabla[s.hour_index][s.day_index] = s

    # Valores válidos de guardia
    from services.imports import GUARD_LABELS

    return _templates(request).TemplateResponse(
        "schedule_edit.html",
        ctx(
            request,
            user,
            teacher=teacher,
            schedule=tabla,
            GUARD_LABELS=sorted(list(GUARD_LABELS)),
        ),
    )


# ======================================================
# POST /schedule/edit/{teacher_id} — guardar edición
# ======================================================
@router.post("/schedule/edit/{teacher_id}")
async def schedule_edit_post(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", 303)

    # Borrar horario actual
    await session.execute(
        ScheduleSlot.__table__.delete().where(ScheduleSlot.teacher_id == teacher_id)
    )

    form = await request.form()

    # 7 franjas x 5 días
    for hour in range(7):
        for day in range(5):
            prefix = f"{hour}_{day}_"
            tipo = form.get(prefix + "type")

            if tipo == "CLASS":
                group = (form.get(prefix + "group") or "").strip()
                room = (form.get(prefix + "room") or "").strip()
                subject = (form.get(prefix + "subject") or "").strip()
                if group and subject:
                    session.add(
                        ScheduleSlot(
                            teacher_id=teacher_id,
                            day_index=day,
                            hour_index=hour,
                            type=ScheduleType.CLASS,
                            group=group,
                            room=room,
                            subject=subject,
                            source="manual",
                        )
                    )

            elif tipo == "GUARD":
                guard_type = (form.get(prefix + "guard_type") or "").strip()
                if guard_type:
                    session.add(
                        ScheduleSlot(
                            teacher_id=teacher_id,
                            day_index=day,
                            hour_index=hour,
                            type=ScheduleType.GUARD,
                            guard_type=guard_type,
                            source="manual",
                        )
                    )

    await session.commit()
    return RedirectResponse("/schedule/view", 303)


# ======================================================
# GET /schedule/print/{teacher_id} — PDF horario
# ======================================================
@router.get("/schedule/print/{teacher_id}")
async def schedule_print(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
):
    teacher = await session.get(Teacher, teacher_id)
    if not teacher:
        raise HTTPException(404, "Profesor no encontrado")

    slots = (
        await session.execute(
            select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id)
        )
    ).scalars().all()

    tabla = [[None for _ in range(5)] for _ in range(7)]
    for s in slots:
        if 0 <= s.hour_index <= 6 and 0 <= s.day_index <= 4:
            if s.type == ScheduleType.CLASS:
                tabla[s.hour_index][s.day_index] = {
                    "type": "CLASS",
                    "group": s.group or "",
                    "room": s.room or "",
                    "subject": s.subject or "",
                }
            else:
                tabla[s.hour_index][s.day_index] = {
                    "type": "GUARD",
                    "guard_type": s.guard_type or "",
                }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    generate_schedule_pdf(
        path=tmp.name,
        teacher_name=teacher.name,
        center_name=settings.INSTITUTION_NAME or "",
        schedule=tabla,
    )

    filename = f"Horario_{teacher.name}.pdf".replace(" ", "_")
    return FileResponse(tmp.name, "application/pdf", filename=filename)
