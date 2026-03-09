# schedule_router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_session
from models import Teacher, ScheduleSlot, ScheduleType
from config import settings

router = APIRouter()


# Helpers de plantillas
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
    teachers = (await session.execute(select(Teacher).order_by(Teacher.name.asc()))).scalars().all()

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

    teachers = (await session.execute(select(Teacher).order_by(Teacher.name.asc()))).scalars().all()

    # Cargar slots del profesor
    slots = (
        await session.execute(select(ScheduleSlot).where(ScheduleSlot.teacher_id == teacher_id))
    ).scalars().all()

    # Crear matriz 7 x 5 (franjas x días)
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
