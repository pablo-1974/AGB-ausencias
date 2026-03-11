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

# --- NUEVO: Iniciar baja ---

from services.leaves import open_leave  # ya importaste close_leave arriba; añadimos open_leave
from fastapi import HTTPException

@router.get("/leaves/new")
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # <-- ajusta si no debe requerir admin
):
    """
    Muestra el formulario de apertura de baja con el listado de profesores.
    """
    # Cargar profesores (solo activos, filtra por status)
    q = select(Teacher).where(Teacher.status == TeacherStatus.activo).order_by(Teacher.name.asc())
    teachers = (await session.execute(q)).scalars().all()

    return _templates(request).TemplateResponse(
        "leaves_new.html",
        _ctx(request, title="Iniciar baja", teachers=teachers),
    )

@router.post("/leaves/new")
async def leaves_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # <-- ajusta si no debe requerir admin
    teacher_id: int = Form(...),
    start_date: date = Form(...),
    leave_type: str = Form("baja"),  # por si luego añades selector ('baja' | 'excedencia')
    cause: str = Form("Baja"),
):
    """
    Procesa el formulario e inicia la baja usando services.leaves.open_leave.
    """
    # Mapear leave_type -> Enum
    lt = TeacherStatus.baja if leave_type == "baja" else TeacherStatus.excedencia

    try:
        await open_leave(
            session=session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=lt,
            cause=cause or "Baja",
        )
        # Redirige a una vista tras crear. Si aún no tienes listado, puedes volver al close o al dashboard.
        # Si crearás /leaves (listado), cámbialo a "/leaves".
        return RedirectResponse("/leaves/close", status_code=303)
    except HTTPException as he:
        # Re-pintar el form con error de validación del servicio
        q = select(Teacher).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()
        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(request, title="Iniciar baja", teachers=teachers, error=he.detail),
            status_code=he.status_code,
        )
    except Exception as e:
        # Error genérico
        q = select(Teacher).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()
        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(request, title="Iniciar baja", teachers=teachers, error=str(e)),
            status_code=400,
        )
