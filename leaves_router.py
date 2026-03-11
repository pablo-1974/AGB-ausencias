# leaves_router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, Request, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from database import get_session
from config import settings
from auth import admin_required
from models import Teacher, TeacherStatus, Leave
from services.leaves import close_leave

router = APIRouter()

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
        "title": "Bajas",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base

@router.get("/leaves/close")
async def leaves_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    # Profesores que TIENEN una baja/excedencia abierta (end_date NULL)
    res = await session.execute(
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(Leave.end_date.is_(None))
        .order_by(Teacher.name.asc())
    )
    rows = res.all()  # (Leave, Teacher)
    open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        _ctx(request, open_items=open_items, title="Finalizar baja"),
    )

@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    try:
        await close_leave(session, teacher_id=teacher_id, end_date=end_date)
        # Recargar la lista para que desaparezca la que acabas de cerrar
        res = await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
            .order_by(Teacher.name.asc())
        )
        rows = res.all()
        open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, open_items=open_items, info="Baja finalizada correctamente."),
        )
    except Exception as e:
        # Volver a pintar el form con el error
        res = await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
            .order_by(Teacher.name.asc())
        )
        rows = res.all()
        open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, open_items=open_items, error=str(e)),
            status_code=400,
        )
