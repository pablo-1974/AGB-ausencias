# admin_router.py
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from starlette.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date

from database import get_session
from auth import admin_required
from models import Teacher, TeacherStatus, Leave, Absence, User
from config import settings

router = APIRouter(prefix="/admin", tags=["admin"])


def _templates(request: Request):
    return request.app.state.templates


def _ctx(request: Request, user: User, **extra):
    from datetime import datetime
    now = datetime.now()

    base = {
        "request": request,
        "user": user,
        "title": "Panel del Administrador",
        "now": now,
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


@router.get("/panel")
async def admin_panel(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    De momento solo cargamos la plantilla.
    Luego añadiremos los KPIs.
    """
    return _templates(request).TemplateResponse(
        "admin_panel.html",
        _ctx(request, user=admin),
    )
