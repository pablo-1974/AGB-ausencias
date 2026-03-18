# imports_teachers.py
from __future__ import annotations

import os
import tempfile
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher, User
from auth import admin_required
from services.imports import import_teachers_from_excel

# 🔥 ORDENACIÓN SIN TILDES
from utils import normalize_name

router = APIRouter()


# -----------------------------
# Helpers plantilla/contexto
# -----------------------------
def _templates(request: Request):
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl


def _ctx(request: Request, user: User, **extra):
    base = {
        "request": request,
        "user": user,
        "title": "Importar profesores",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# --------------------------------------------------------
# GET /imports/teachers — formulario
# --------------------------------------------------------
@router.get("/imports/teachers")
async def imports_teachers_form(
    request: Request,
    admin: User = Depends(admin_required),
):
    user = admin
    return _templates(request).TemplateResponse(
        "teachers_import.html",
        _ctx(request, user=user),
    )


# --------------------------------------------------------
# POST /imports/teachers — subir Excel
# --------------------------------------------------------
@router.post("/imports/teachers")
async def imports_teachers_upload(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = admin

    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, user=user, error="Formato no soportado. Sube un .xlsx o .xls."),
            status_code=400,
        )

    tmp_path = None
    try:
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        imported = await import_teachers_from_excel(tmp_path, session)

        # 🔥 ORDENACIÓN IMPORTADA
        imported = sorted(imported, key=lambda it: normalize_name(it.get("name", "")))

        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, user=user, imported=imported),
        )

    except Exception as e:
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            _ctx(request, user=user, error=f"Error importando: {e}"),
            status_code=400,
        )
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass


# --------------------------------------------------------
# GET /teachers — listado de profesores importados
# --------------------------------------------------------
@router.get("/teachers")
async def teachers_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = admin

    # 🔥 ANTES: order_by SQL → AHORA orden Python con normalize_name
    res = await session.execute(select(Teacher))
    teachers = res.scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "teachers_list.html",
        _ctx(request, user=user, teachers=teachers, title="Profesores"),
    )
