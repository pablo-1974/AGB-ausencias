# ======================================================
# config_calendar_router.py — CONFIGURACIÓN DEL CALENDARIO ESCOLAR
# ======================================================
# Contiene:
#   - Configuración del calendario escolar
#   - Vista anual (12 meses)
#   - Edición del calendario
#   - Añadir / borrar festivos
#
# Totalmente adaptado a ctx() y a JSONB seguro para other_holidays.
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

from context import ctx

router = APIRouter(prefix="/config/calendar", tags=["calendar"])


# ------------------------------------------------------
# Helpers plantillas
# ------------------------------------------------------
def _templates(request: Request):
    return request.app.state.templates


# ------------------------------------------------------
# GET /
# ------------------------------------------------------
@router.get("/")
async def calendar_get(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    return _templates(request).TemplateResponse(
        "calendar_config.html",
        ctx(request, admin, title="Configuración del calendario", calendar=cal),
    )


# ------------------------------------------------------
# POST /
# ------------------------------------------------------
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
    fd = date.fromisoformat(first_day)
    ld = date.fromisoformat(last_day)
    xs = date.fromisoformat(xmas_start)
    xe = date.fromisoformat(xmas_end)
    es = date.fromisoformat(easter_start)
    ee = date.fromisoformat(easter_end)

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

    return RedirectResponse("/config/calendar/view", 303)


# ------------------------------------------------------
# GET /view
# ------------------------------------------------------
@router.get("/view")
async def calendar_view(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    if not cal:
        return RedirectResponse("/config/calendar/", 303)

    months = []
    cur = cal.first_day.replace(day=1)

    for _ in range(12):
        year = cur.year
        month = cur.month
        m1 = cur

        if month == 12:
            next_m = cur.replace(year=year + 1, month=1, day=1)
        else:
            next_m = cur.replace(month=month + 1, day=1)

        m_last = next_m - timedelta(days=1)

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


# ------------------------------------------------------
# GET /edit
# ------------------------------------------------------
@router.get("/edit")
async def calendar_edit(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required)
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    if not cal:
        return RedirectResponse("/config/calendar/", 303)

    return _templates(request).TemplateResponse(
        "calendar_edit.html",
        ctx(request, admin, title="Editar calendario", calendar=cal),
    )


# ------------------------------------------------------
# POST /edit
# ------------------------------------------------------
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
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    if not cal:
        return RedirectResponse("/config/calendar/", 303)

    cal.school_year = school_year
    cal.first_day = date.fromisoformat(first_day)
    cal.last_day = date.fromisoformat(last_day)
    cal.xmas_start = date.fromisoformat(xmas_start)
    cal.xmas_end = date.fromisoformat(xmas_end)
    cal.easter_start = date.fromisoformat(easter_start)
    cal.easter_end = date.fromisoformat(easter_end)

    # YA NO TOCAMOS other_holidays AQUÍ

    await session.commit()

    return RedirectResponse("/config/calendar/view", 303)


# ------------------------------------------------------
# POST /delete-holiday
# ------------------------------------------------------
@router.post("/delete-holiday")
async def calendar_delete_holiday(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    holiday_date: str = Form(...),
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    if not cal:
        return RedirectResponse("/config/calendar/", 303)

    existing = cal.other_holidays
    if not isinstance(existing, list):
        existing = [h.strip() for h in str(existing).split(",") if h.strip()]

    cal.other_holidays = [h for h in existing if h != holiday_date]

    await session.commit()

    return RedirectResponse("/config/calendar/edit", 303)


# ------------------------------------------------------
# POST /add-holiday
# ------------------------------------------------------
@router.post("/add-holiday")
async def calendar_add_holiday(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    holiday_date: str = Form(...),
):
    cal = await session.scalar(
        select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
    )

    if not cal:
        return RedirectResponse("/config/calendar/", 303)
    print("POST holiday_date recibido =", repr(holiday_date)) # PROVISIONAL
    new_date = holiday_date.strip()

    existing = cal.other_holidays
    if not isinstance(existing, list):
        existing = [h.strip() for h in str(existing).split(",") if h.strip()]

    if new_date not in existing:
        existing.append(new_date)

    cal.other_holidays = existing
    await session.commit()

    return RedirectResponse("/config/calendar/edit", 303)
