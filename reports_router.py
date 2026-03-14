# reports_router.py
from __future__ import annotations

from datetime import date, timedelta, datetime
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
        "now": datetime.now(),
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

# ==================================
# PARTE MENSUAL (GET formulario)
# ==================================
@router.get("/reports/monthly", response_class=HTMLResponse)
async def reports_monthly_form(
    request: Request,
    admin=Depends(admin_required),
):
    # Primer y último día del mes anterior
    today = date.today()
    first_last_month = date(today.year, today.month - 1, 1) if today.month > 1 else date(today.year - 1, 12, 1)

    # último día del mes anterior
    if first_last_month.month == 12:
        last_last_month = date(first_last_month.year, 12, 31)
    else:
        # último día = día antes del primer día del mes actual
        last_last_month = date(today.year, today.month, 1) - timedelta(days=1)

    return _templates(request).TemplateResponse(
        "reports_monthly.html",
        _ctx(
            request,
            title="Parte mensual de ausencias",
            date_from=first_last_month.isoformat(),
            date_to=last_last_month.isoformat(),
        ),
    )

# ==================================
# PARTE MENSUAL — Vista previa
# ==================================
@router.get("/reports/monthly/view", response_class=HTMLResponse)
async def reports_monthly_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    date_from: date = Query(...),
    date_to: date = Query(...),
):
    from services.pdf_monthly import build_monthly_report_pdf

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    has_uncategorized, rows, rows_html = await build_monthly_report_pdf(
        session=session,
        date_from=date_from,
        date_to=date_to,
        path_out=tmp.name,
    )

    return _templates(request).TemplateResponse(
        "reports_monthly.html",
        _ctx(
            request,
            title="Parte mensual de ausencias",
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            rows=rows_html,                  # MOSTRAMOS TABLA SIEMPRE
            has_uncategorized=has_uncategorized,
        ),
    )

# ==================================
# PARTE MENSUAL — Generación PDF
# ==================================
@router.get("/reports/monthly/pdf")
async def reports_monthly_pdf(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    date_from: date = Query(...),
    date_to: date = Query(...),
):
    from services.pdf_monthly import build_monthly_report_pdf

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    has_uncategorized, rows = await build_monthly_report_pdf(
        session=session,
        date_from=date_from,
        date_to=date_to,
        path_out=tmp.name,
    )

    # ❗ NO permitir PDF si hay elementos sin catalogar
    if has_uncategorized:
        return _templates(request).TemplateResponse(
            "reports_monthly.html",
            _ctx(
                request,
                title="Parte mensual de ausencias",
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                rows=rows,
                has_uncategorized=True,
                pdf_error="No se puede generar el PDF porque hay AUSENCIAS o BAJAS sin catalogar.",
            ),
            status_code=400,
        )

    filename = f"parte_mensual_{date_from}_{date_to}.pdf"
    return FileResponse(tmp.name, media_type="application/pdf", filename=filename)

