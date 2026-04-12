# ======================================================
# reports_router.py — GENERACIÓN DE INFORMES
# ======================================================
# Informes diarios y mensuales de ausencias:
#   - Parte diario (vista previa + PDF)
#   - Parte mensual (vista previa + PDF)
#
# Todas las rutas usan el contexto global ctx() para
# unificar variables del header (fecha/hora/user/logo).
# ======================================================

# Nota:
# El cálculo de bajas mensuales NO replica la lógica diaria de sustituciones.
# Se usan intervalos consolidados por profesor real afectado.

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
import tempfile

from database import get_session
from config import settings
from auth import admin_required
from models import User

from services.pdf_daily import build_daily_report_pdf, build_daily_report_data

# 🔥 Contexto global
from context import ctx

router = APIRouter(tags=["reports"])


# ======================================================
# Helpers de plantillas
# ======================================================
def _templates(request: Request):
    """Accede al motor de plantillas (Jinja2)."""
    return request.app.state.templates


# ======================================================
# PARTE DIARIO — formulario inicial
# ======================================================
@router.get("/reports/daily", response_class=HTMLResponse)
async def reports_daily_form(
    request: Request,
    admin: User = Depends(admin_required),
):
    """Formulario inicial del parte diario."""
    today_str = date.today().isoformat()

    return _templates(request).TemplateResponse(
        "reports_daily.html",
        ctx(request, admin, title="Parte diario de ausencias", today=today_str),
    )


# ======================================================
# PARTE DIARIO — vista previa
# ======================================================
@router.get("/reports/daily/view", response_class=HTMLResponse)
async def reports_daily_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    date: date = Query(...),
    obs: Optional[str] = Query(None),
):
    """Vista previa del parte diario antes de generar PDF."""
    preview = await build_daily_report_data(
        session=session,
        the_date=date,
        observaciones_usuario=(obs or "").strip() or None,
    )

    return _templates(request).TemplateResponse(
        "reports_daily.html",
        ctx(
            request,
            admin,
            title="Parte diario de ausencias",
            today=date.isoformat(),
            preview=preview,
            observaciones_prefill=(obs or ""),
        ),
    )


# ======================================================
# PARTE DIARIO — generación de PDF
# ======================================================
@router.post("/reports/daily")
async def reports_daily_generate(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    date: date = Form(...),
    obs: Optional[str] = Form(None),
):
    """Genera el PDF del parte diario."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    await build_daily_report_pdf(
        session=session,
        the_date=date,
        path_out=tmp.name,
        observaciones_usuario=(obs or "").strip() or None,
    )

    filename = f"parte_diario_{date.isoformat()}.pdf"
    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=filename
    )


# ======================================================
# PARTE MENSUAL — formulario
# ======================================================
@router.get("/reports/monthly", response_class=HTMLResponse)
async def reports_monthly_form(
    request: Request,
    admin: User = Depends(admin_required),
):
    """Formulario para seleccionar rango mensual."""
    today = date.today()
    prev_month_start = (
        date(today.year, today.month - 1, 1)
        if today.month > 1 else date(today.year - 1, 12, 1)
    )

    # Último día del mes anterior
    if prev_month_start.month == 12:
        prev_month_end = date(prev_month_start.year, 12, 31)
    else:
        prev_month_end = date(today.year, today.month, 1) - timedelta(days=1)

    return _templates(request).TemplateResponse(
        "reports_monthly.html",
        ctx(
            request,
            admin,
            title="Parte mensual de ausencias",
            date_from=prev_month_start.isoformat(),
            date_to=prev_month_end.isoformat(),
        ),
    )


# ======================================================
# PARTE MENSUAL — vista previa
# ======================================================
@router.get("/reports/monthly/view", response_class=HTMLResponse)
async def reports_monthly_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    date_from: date = Query(...),
    date_to: date = Query(...),
):
    """Vista previa del parte mensual."""
    from services.pdf_monthly import build_monthly_report_pdf

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    has_uncategorized, rows = await build_monthly_report_pdf(
        session=session,
        date_from=date_from,
        date_to=date_to,
        path_out=tmp.name,
    )
    
    rows_catalogadas = []
    rows_sin_catalogar = []
    
    for r in rows:
        catalogacion = (r[3] or "").strip()
        if catalogacion and catalogacion != "Z":
            rows_catalogadas.append(r)
        else:
            rows_sin_catalogar.append(r)
    
    has_uncategorized = len(rows_sin_catalogar) > 0
    
    return _templates(request).TemplateResponse(
        "reports_monthly.html",
        ctx(
            request,
            admin,
            title="Parte mensual de ausencias",
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            rows_catalogadas=rows_catalogadas,
            rows_sin_catalogar=rows_sin_catalogar,
            has_uncategorized=has_uncategorized,
        ),
    )


# ======================================================
# PARTE MENSUAL — generación PDF
# ======================================================
@router.get("/reports/monthly/pdf")
async def reports_monthly_pdf(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    date_from: date = Query(...),
    date_to: date = Query(...),
):
    """Genera el PDF del parte mensual, si todo está catalogado."""
    from services.pdf_monthly import build_monthly_report_pdf

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    has_uncategorized, rows = await build_monthly_report_pdf(
        session=session,
        date_from=date_from,
        date_to=date_to,
        path_out=tmp.name,
    )

    if has_uncategorized:
        return _templates(request).TemplateResponse(
            "reports_monthly.html",
            ctx(
                request,
                admin,
                title="Parte mensual de ausencias",
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                rows=rows,
                has_uncategorized=True,
                pdf_error="No se puede generar el PDF porque hay ausencias o bajas sin catalogar.",
            ),
            status_code=400,
        )

    filename = f"parte_mensual_{date_from}_{date_to}.pdf"
    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=filename
    )
