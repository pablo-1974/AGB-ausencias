# imports_teachers.py
from __future__ import annotations

import os
import tempfile
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher
from auth import admin_required
from services.imports import import_teachers_from_excel  # ya lo tienes

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
        "title": "Importar profesores",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


@router.get("/imports/teachers")
async def imports_teachers_form(
    request: Request,
    admin=Depends(admin_required),  # proteger
):
    # Tu plantilla ya se llama teachers_import.html
    return _templates(request).TemplateResponse(
        "teachers_import.html", _ctx(request)
    )


@router.post("/imports/teachers")
async def imports_teachers_upload(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # proteger
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, error="Formato no soportado. Sube un .xlsx o .xls."),
            status_code=400,
        )

    tmp_path = None
    try:
        # Guardar a un archivo temporal con la extensión correcta (xlsx/xls)
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Llamar a tu servicio
        imported = await import_teachers_from_excel(tmp_path, session)

        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, imported=imported),
        )

    except Exception as e:
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, error=f"Error importando: {e}"),
            status_code=400,
        )
    finally:
        # Limpiar el archivo temporal
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


@router.get("/teachers")
async def teachers_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # puedes quitarlo si quieres que lo vea cualquiera autenticado
):
    res = await session.execute(select(Teacher).order_by(Teacher.name.asc()))
    teachers = res.scalars().all()
    return _templates(request).TemplateResponse(
        "teachers_list.html",
        _ctx(request, teachers=teachers, title="Profesores"),
    )
