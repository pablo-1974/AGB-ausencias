# ======================================================
# config_calendar_router.py — CONFIGURACIÓN DEL CALENDARIO ESCOLAR
# ======================================================
# Contiene:
#   - Configuración del calendario escolar (formulario)
#   - Vista de los 12 meses (procesada)
#   - Guardado del calendario
#   - Edición posterior del calendario (añadir/quitar festivos, ajustar fechas)
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
# GET /config/calendar  — formulario de configuración inicial
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
# POST /config/calendar  — Guardar configuración inicial
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

    # Tras guardar → vista limpia
    return RedirectResponse("/config/calendar/view", 303)


# ======================================================
# GET /config/calendar/view  — vista anual (12 meses)
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
        m1 = cur

        # Inicio del mes siguiente
        if month == 12:
            next_m = cur.replace(year=year + 1, month=1, day=1)
        else:
            next_m = cur.replace(month=month + 1, day=1)

        m_last = next_m - timedelta(days=1)

        # Recorrer días
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
            elif d.isoformat() in (cal.other_holidays or []):
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
# GET /admin/edit/calendar  — Edición completa del calendario
# ======================================================
@router.get("/edit")
async def calendar_edit(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required)
):
    """Pantalla completa para editar fechas y festivos."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        return RedirectResponse("/config/calendar", 303)

    return _templates(request).TemplateResponse(
        "calendar_edit.html",
        ctx(request, admin, title="Editar calendario", calendar=cal),
    )


# ======================================================
# POST /admin/edit/calendar  — Guardar cambios de edición
# ======================================================
@router.post("/edit")
async def calendar_edit_post(
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
    """Guarda los cambios en un calendario existente."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        return RedirectResponse("/config/calendar", 303)

    cal.school_year = school_year
    cal.first_day = date.fromisoformat(first_day)
    cal.last_day = date.fromisoformat(last_day)
    cal.xmas_start = date.fromisoformat(xmas_start)
    cal.xmas_end = date.fromisoformat(xmas_end)
    cal.easter_start = date.fromisoformat(easter_start)
    cal.easter_end = date.fromisoformat(easter_end)

    # Procesar festivos
    festivos = [h.strip() for h in other_holidays.split(",") if h.strip()]
    cal.other_holidays = festivos

    await session.commit()

    return RedirectResponse("/config/calendar/view", 303)

# ======================================================
# POST /config/calendar/delete-holiday  — borrar festivo individual
# ======================================================
@router.post("/delete-holiday")
async def calendar_delete_holiday(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    holiday_date: str = Form(...),
):
    """Elimina un festivo individual del calendario."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        return RedirectResponse("/config/calendar/view", 303)

    # eliminar del array
    cal.other_holidays = [h for h in (cal.other_holidays or []) if h != holiday_date]

    await session.commit()
    return RedirectResponse("/config/calendar/edit", 303)

# ======================================================
# POST /config/calendar/add-holiday — añadir festivo individual
# ======================================================
@router.post("/add-holiday")
async def calendar_add_holiday(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    holiday_date: str = Form(...),
):
    """Añade un festivo individual al calendario."""
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        return RedirectResponse("/config/calendar", 303)

    new_date = holiday_date.strip()

    if new_date:
        existing = cal.other_holidays or []
        if new_date not in existing:
            existing.append(new_date)
            cal.other_holidays = existing
            await session.commit()

    return RedirectResponse("/config/calendar/edit", 303)
