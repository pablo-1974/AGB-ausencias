# ======================================================
# imports_teachers.py — IMPORTACIÓN DE PROFESORADO
# ======================================================
# Permite subir un Excel (.xlsx/.xls) con datos de profesor@s
# y procesarlo mediante import_teachers_from_excel().
#
# Todas las plantillas pasan ahora por el contexto global ctx(),
# garantizando coherencia visual, fecha/hora en el header y datos comunes.
# ======================================================

from __future__ import annotations

import os
import tempfile
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher, User
from auth import admin_required
from services.imports import import_teachers_from_excel

from utils import normalize_name

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter()


# ======================================================
# Helpers de plantillas
# ======================================================
def _templates(request: Request):
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl


# ======================================================
# GET /imports/teachers — formulario de importación
# ======================================================
@router.get("/imports/teachers")
async def imports_teachers_form(
    request: Request,
    admin: User = Depends(admin_required),
):
    return _templates(request).TemplateResponse(
        "teachers_import.html",
        ctx(request, admin, title="Importar profesores"),
    )


# ======================================================
# POST /imports/teachers — subir y procesar Excel
# ======================================================
@router.post("/imports/teachers")
async def imports_teachers_upload(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Procesa un Excel y muestra la lista importada."""
    user = admin

    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            ctx(request, user, error="Formato no soportado. Sube un .xlsx o .xls."),
            status_code=400,
        )

    tmp_path = None
    try:
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        imported = await import_teachers_from_excel(tmp_path, session)

        # Ordenación alfabética correcta
        imported = sorted(imported, key=lambda it: normalize_name(it.get("name", "")))

        return _templates(request).TemplateResponse(
            "teachers_import.html",
            ctx(request, user, imported=imported, title="Importar profesores"),
        )

    except Exception as e:
        return _templates(request).TemplateResponse(
            "teachers_import.html",
            ctx(request, user, error=f"Error importando: {e}", title="Importar profesores"),
            status_code=400,
        )

    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass


# ======================================================
# GET /teachers — listado tras importación
# ======================================================
@router.get("/teachers")
async def teachers_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Listado de profesores importados (solo admin)."""
    user = admin

    res = await session.execute(select(Teacher))
    teachers = res.scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "teachers_list.html",
        ctx(request, user, teachers=teachers, title="Profesores"),
    )
