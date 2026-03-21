# ======================================================
# config_calendar_router.py — CONFIGURACIÓN DEL CALENDARIO ESCOLAR
# ======================================================
# Contiene:
#   - Configuración del calendario escolar (formulario)
#   - Vista de los 12 meses (procesada)
#   - Guardado del calendario
#
# Todas las rutas usan el contexto global ctx() para asegurar
# coherencia en el header (fecha/hora), menú y datos comunes.
# ======================================================

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import SchoolCalendar, User
from auth import admin_required
from datetime import date, timedelta

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter(prefix="/config/calendar", tags=["calendar"])


# ======================================================
# Helpers de plantillas (mantiene solo el acceso a Jinja2)
# ======================================================
def _templates(request: Request):
    return request.app.state.templates


# ======================================================
# GET /config/calendar  — formulario de configuración
# ======================================================
@router.get("/")
async def calendar_get(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Muestra el formulario de configuración del calendario escolar."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    return _templates(request).TemplateResponse(
        "calendar_config.html",
        ctx(request, admin, title="Configuración del calendario", calendar=cal),
    )


# ======================================================
# GET /config/calendar/view  — vista de los 12 meses
# ======================================================
@router.get("/view")
async def calendar_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Muestra la vista completa del calendario escolar (12 meses)."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        return RedirectResponse("/config/calendar", 303)

    # Construcción de estructura de meses
    months = []
    cur = cal.first_day.replace(day=1)

    for _ in range(12):
        year = cur.year
        month = cur.month

        # Inicio del mes
        m1 = cur

        # Inicio del siguiente mes
        if month == 12:
            next_m = cur.replace(year=year + 1, month=1, day=1)
        else:
            next_m = cur.replace(month=month + 1, day=1)

        # Fin del mes
        m_last = next_m - timedelta(days=1)

        # Recorrer días del mes
        days = []
        d = m1
        while d <= m_last:
            if d.weekday() >= 5:
                kind = "weekend"
            elif d < cal.first_day or d > cal.last_day:
                kind = "out"
            elif cal.xmas_start <= d <= cal.xmas_end:
                kind = "xmas"
            elif cal.easter_start <= d <= cal.easter_end:
                kind = "easter"
            elif isinstance(cal.other_holidays, list) and d.isoformat() in cal.other_holidays:
                kind = "holiday"
            else:
                kind = "class"

            days.append((d.day, d.weekday(), kind))
            d += timedelta(days=1)

        months.append({
            "name": m1.strftime("%B").upper(),
            "year": m1.year,
            "first_weekday": m1.weekday(),
            "days": days,
        })

        cur = next_m

    return _templates(request).TemplateResponse(
        "calendar_view.html",
        ctx(
            request,
            admin,
            title="Calendario escolar",
            calendar=cal,
            months=months,
        ),
    )


# ======================================================
# POST /config/calendar  — Guardar configuración
# ======================================================
@router.post("/")
async def calendar_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    school_year: str = Form(...),
    first_day: str = Form(...),
    last_day: str = Form(...),
    xmas_start: str = Form(...),
    xmas_end: str = Form(...),
    easter_start: str = Form(...),
    easter_end: str = Form(...),
    other_holidays: str = Form(""),
):
    """Guarda un nuevo calendario escolar."""
    fd = date.fromisoformat(first_day)
    ld = date.fromisoformat(last_day)
    xs = date.fromisoformat(xmas_start)
    xe = date.fromisoformat(xmas_end)
    es = date.fromisoformat(easter_start)
    ee = date.fromisoformat(easter_end)

    # Lista de festivos adicionales
    festivos = [h.strip() for h in other_holidays.split(",") if h.strip()]

    cal = SchoolCalendar(
        school_year=school_year,
        first_day=fd,
        last_day=ld,
        xmas_start=xs,
        xmas_end=xe,
        easter_start=es,
        easter_end=ee,
        other_holidays=festivos,
    )

    session.add(cal)
    await session.commit()

    return RedirectResponse("/config/calendar", 303)
