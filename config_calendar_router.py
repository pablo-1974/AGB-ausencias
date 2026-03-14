# config_calendar_router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import SchoolCalendar
from auth import admin_required
from datetime import date

router = APIRouter(prefix="/config/calendar", tags=["calendar"])


# -----------------------------------------
# GET → Mostrar la configuración actual
# -----------------------------------------
@router.get("/")
async def calendar_get(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    return request.app.state.templates.TemplateResponse(
        "calendar_config.html",
        {
            "request": request,
            "title": "Calendario escolar",
            "calendar": cal,
        },
    )


# -----------------------------------------
# VISTA DEL CALENDARIO (12 meses con colores)
# -----------------------------------------
@router.get("/view")
async def calendar_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    # Cargar calendario escolar
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if not cal:
        # Si no hay calendario aún → redirigir al editor
        return RedirectResponse("/config/calendar", status_code=303)

    # Generar meses del curso
    from datetime import timedelta

    months = []
    cur = cal.first_day

    # ir al 1 del mes
    cur = cur.replace(day=1)

    # generar 12 meses
    for i in range(12):
        year = cur.year
        month = cur.month

        # primer día del mes
        m1 = cur
        # siguiente mes
        if month == 12:
            next_m = cur.replace(year=year+1, month=1, day=1)
        else:
            next_m = cur.replace(month=month+1, day=1)
        m_last = next_m - timedelta(days=1)

        # lista de días del mes
        days = []
        d = m1
        while d <= m_last:
            # determinar tipo
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
            "first_weekday": m1.weekday(),  # 0 lunes - 6 domingo
            "days": days,
        })

        cur = next_m  # avanzar

    return request.app.state.templates.TemplateResponse(
        "calendar_view.html",
        {
            "request": request,
            "title": "Calendario escolar",
            "calendar": cal,
            "months": months,
        },
    )


# -----------------------------------------
# POST → Guardar / actualizar calendario
# -----------------------------------------
@router.post("/")
async def calendar_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),

    school_year: str = Form(...),
    first_day: str = Form(...),
    last_day: str = Form(...),
    xmas_start: str = Form(...),
    xmas_end: str = Form(...),
    easter_start: str = Form(...),
    easter_end: str = Form(...),
    other_holidays: str = Form(""),
):
    from datetime import date

    # Convertir texto ISO → date
    fd = date.fromisoformat(first_day)
    ld = date.fromisoformat(last_day)
    xs = date.fromisoformat(xmas_start)
    xe = date.fromisoformat(xmas_end)
    es = date.fromisoformat(easter_start)
    ee = date.fromisoformat(easter_end)

    # Convertir lista de festivos separados por coma
    festivos = [h.strip() for h in other_holidays.split(",") if h.strip()]

    # Crear registro
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

    return RedirectResponse("/config/calendar", status_code=303)
