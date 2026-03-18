# schedule_router.py
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

# 🔥 Importamos la dependencia correcta del usuario
from app import load_user_dep

from utils import normalize_name

router = APIRouter()


# -------------------------------
# Helpers de plantillas
# -------------------------------
def _templates(request: Request):
    return request.app.state.templates


def _ctx(request: Request, user: User, **extra):
    """
    Contexto uniforme para TODAS las plantillas del módulo.
    Incluye SIEMPRE:
    - request
    - user  (necesario para base.html y el menú)
    - app_name, institution_name, logo_path
    - y cualquier dato extra
    """
    base = {
        "request": request,
        "user": user,  # 🔥 CLAVE para menú/cabecera
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
        "title": "Horario",
    }
    base.update(extra or {})
    return base


# ---------------------------------------------------------
#  GET /schedule/view — selector vacío
# ---------------------------------------------------------
@router.get("/schedule/view")
async def schedule_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),      # 🔥 añadimos usuario
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    teachers = (await session.execute(select(Teacher))).scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "schedule_view.html",
        _ctx(request, user=user, teachers=teachers, selected_id=None, schedule=None),
    )


# ---------------------------------------------------------
#  POST /schedule/view — mostrar horario
# ---------------------------------------------------------
@router.post("/schedule/view")
async def schedule_view_post(
    request: Request,
    teacher_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),     # 🔥 usuario
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    teachers = (await session.execute(select(Teacher))).scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    # Cargar slots del profesor
    slots = (
        await session.execute(
            select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id)
        )
    ).scalars().all()

    # Crear matriz 7x5 (franjas x días)
    tabla = [[None for _ in range(5)] for _ in range(7)]
    for s in slots:
        if 0 <= s.hour_index <= 6 and 0 <= s.day_index <= 4:
            tabla[s.hour_index][s.day_index] = s

    return _templates(request).TemplateResponse(
        "schedule_view.html",
        _ctx(
            request,
            user=user,
            teachers=teachers,
            selected_id=teacher_id,
            schedule=tabla,
        ),
    )


# ---------------------------------------------------------
#  GET /schedule/edit/{teacher_id}
# ---------------------------------------------------------
@router.get("/schedule/edit/{teacher_id}")
async def schedule_edit(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),     # 🔥 usuario
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    teacher = await session.get(Teacher, teacher_id)
    if not teacher:
        return RedirectResponse("/schedule/view", status_code=303)

    slots = (
        await session.execute(
            select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id)
        )
    ).scalars().all()

    tabla = [[None for _ in range(5)] for _ in range(7)]
    for s in slots:
        if 0 <= s.hour_index <= 6 and 0 <= s.day_index <= 4:
            tabla[s.hour_index][s.day_index] = s

    # Valores de guardia admitidos
    from services.imports import GUARD_LABELS

    return _templates(request).TemplateResponse(
        "schedule_edit.html",
        _ctx(
            request,
            user=user,
            teacher=teacher,
            schedule=tabla,
            GUARD_LABELS=sorted(list(GUARD_LABELS)),
        ),
    )


# ---------------------------------------------------------
#  POST /schedule/edit/{teacher_id}
# ---------------------------------------------------------
@router.post("/schedule/edit/{teacher_id}")
async def schedule_edit_post(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),     # 🔥 usuario
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Borrar slots existentes del profesor
    await session.execute(
        ScheduleSlot.__table__.delete().where(ScheduleSlot.teacher_id == teacher_id)
    )

    form = await request.form()

    # Recorrer celdas (7 franjas x 5 días)
    for hour in range(7):
        for day in range(5):
            prefix = f"{hour}_{day}_"
            tipo = form.get(prefix + "type")  # NONE / CLASS / GUARD

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

    # Volver a la vista del horario (con selector)
    return RedirectResponse("/schedule/view", status_code=303)


# ---------------------------------------------------------
#  GET /schedule/print/{teacher_id} — Descargar PDF
# ---------------------------------------------------------
@router.get("/schedule/print/{teacher_id}")
async def schedule_print(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
):
    teacher = await session.get(Teacher, teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Profesor no encontrado")

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
            elif s.type == ScheduleType.GUARD:
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
    return FileResponse(tmp.name, media_type="application/pdf", filename=filename)
