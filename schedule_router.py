# schedule_router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models import Teacher, ScheduleSlot, ScheduleType
from config import settings

router = APIRouter()


# -------------------------------
# Helpers de plantillas
# -------------------------------
def _templates(request: Request):
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl


def _ctx(request: Request, **extra):
    base = {
        "request": request,
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
async def schedule_view(request: Request, session: AsyncSession = Depends(get_session)):
    teachers = (
        await session.execute(select(Teacher).order_by(Teacher.name.asc()))
    ).scalars().all()

    return _templates(request).TemplateResponse(
        "schedule_view.html",
        _ctx(request, teachers=teachers, selected_id=None, schedule=None)
    )


# ---------------------------------------------------------
#  POST /schedule/view — mostrar horario
# ---------------------------------------------------------
@router.post("/schedule/view")
async def schedule_view_post(
    request: Request,
    teacher_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    teachers = (
        await session.execute(select(Teacher).order_by(Teacher.name.asc()))
    ).scalars().all()

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
            teachers=teachers,
            selected_id=teacher_id,
            schedule=tabla,
        )
    )


# ---------------------------------------------------------
#  GET /schedule/edit/{teacher_id}
# ---------------------------------------------------------
@router.get("/schedule/edit/{teacher_id}")
async def schedule_edit(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
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
        if s.hour_index <= 6 and s.day_index <= 4:
            tabla[s.hour_index][s.day_index] = s

    from services.imports import GUARD_LABELS

    return _templates(request).TemplateResponse(
        "schedule_edit.html",
        _ctx(
            request,
            teacher=teacher,
            schedule=tabla,
            GUARD_LABELS=sorted(list(GUARD_LABELS)),
        )
    )


# ---------------------------------------------------------
#  POST /schedule/edit/{teacher_id}
#  Guarda los cambios del horario
# ---------------------------------------------------------
@router.post("/schedule/edit/{teacher_id}")
async def schedule_edit_post(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    # Borrar slots existentes
    await session.execute(
        ScheduleSlot.__table__.delete().where(ScheduleSlot.teacher_id == teacher_id)
    )

    form = await request.form()

    # Recorrer celdas
    for hour in range(7):
        for day in range(5):
            prefix = f"{hour}_{day}_"
            tipo = form.get(prefix + "type")  # NONE / CLASS / GUARD

            if tipo == "CLASS":
                group = form.get(prefix + "group", "").strip()
                room = form.get(prefix + "room", "").strip()
                subject = form.get(prefix + "subject", "").strip()
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
                guard_type = form.get(prefix + "guard_type", "").strip()
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

    return RedirectResponse("/schedule/view", status_code=303)
