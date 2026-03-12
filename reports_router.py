# reports_router.py
from __future__ import annotations

from datetime import date
from typing import Optional, Any, Dict

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
import tempfile

from database import get_session
from config import settings
from auth import admin_required
from services.pdf_daily import build_daily_report_pdf, build_daily_report_data

router = APIRouter(tags=["reports"])

# -----------------------------
# Helpers de plantillas/contexto
# -----------------------------
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
        "title": "Informes",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# ==================================
# PARTE DIARIO (GET form)
# ==================================
@router.get("/reports/daily", response_class=HTMLResponse)
async def reports_daily_form(
    request: Request,
    admin=Depends(admin_required),
):
    today_str = date.today().isoformat()
    return _templates(request).TemplateResponse(
        "reports_daily.html",
        _ctx(request, title="Parte diario de ausencias", today=today_str),
    )


# ==================================
# PARTE DIARIO (GET vista en pantalla)
# ==================================
@router.get("/reports/daily/view", response_class=HTMLResponse)
async def reports_daily_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    fecha: date = Query(...),
    observaciones: Optional[str] = Query(None),
):
    # Construimos los datos reutilizando la lógica del PDF
    preview = await build_daily_report_data(
        session=session,
        the_date=fecha,
        observaciones_usuario=(observaciones or "").strip() or None,
    )
    return _templates(request).TemplateResponse(
        "reports_daily.html",
        _ctx(
            request,
            title="Parte diario de ausencias",
            today=fecha.isoformat(),
            preview=preview,  # <-- la plantilla pintará la tabla si viene esto
            observaciones_prefill=(observaciones or ""),
        ),
    )


# ==================================
# PARTE DIARIO (POST PDF)
# ==================================
@router.post("/reports/daily")
async def reports_daily_generate(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    fecha: date = Form(...),
    observaciones: Optional[str] = Form(None),
):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    await build_daily_report_pdf(
        session=session,
        the_date=fecha,
        path_out=tmp.name,
        observaciones_usuario=(observaciones or "").strip() or None,
    )

    filename = f"parte_diario_{fecha.isoformat()}.pdf"
    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=filename,
    )
