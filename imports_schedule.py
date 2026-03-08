# imports_schedule.py
from __future__ import annotations
import os, tempfile
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from auth import admin_required
from services.imports import import_guards_from_excel, import_classes_from_excel

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
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base

# -------- GUARDIAS --------
@router.get("/imports/guards")
async def guards_import_form(request: Request, admin=Depends(admin_required)):
    return _templates(request).TemplateResponse("guards_import.html", _ctx(request, title="Importar guardias"))

@router.post("/imports/guards")
async def guards_import_upload(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return _templates(request).TemplateResponse(
            "guards_import.html",
            _ctx(request, title="Importar guardias", error="Formato no soportado. Sube un .xlsx o .xls."),
            status_code=400,
        )

    tmp_path = None
    try:
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        cnt = await import_guards_from_excel(tmp_path, session)

        return _templates(request).TemplateResponse(
            "guards_import.html",
            _ctx(request, title="Importar guardias", imported=cnt, info="Importación completada."),
        )
    except Exception as e:
        return _templates(request).TemplateResponse(
            "guards_import.html",
            _ctx(request, title="Importar guardias", error=f"Error importando: {e}"),
            status_code=400,
        )
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

# -------- CLASES --------
@router.get("/imports/classes")
async def classes_import_form(request: Request, admin=Depends(admin_required)):
    return _templates(request).TemplateResponse("classes_import.html", _ctx(request, title="Importar horas de clase"))

@router.post("/imports/classes")
async def classes_import_upload(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls")):
        return _templates(request).TemplateResponse(
            "classes_import.html",
            _ctx(request, title="Importar horas de clase", error="Formato no soportado. Sube un .xlsx o .xls."),
            status_code=400,
        )

    tmp_path = None
    try:
        suffix = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        cnt = await import_classes_from_excel(tmp_path, session)

        return _templates(request).TemplateResponse(
            "classes_import.html",
            _ctx(request, title="Importar horas de clase", imported=cnt, info="Importación completada."),
        )
    except Exception as e:
        return _templates(request).TemplateResponse(
            "classes_import.html",
            _ctx(request, title="Importar horas de clase", error=f"Error importando: {e}"),
            status_code=400,
        )
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
