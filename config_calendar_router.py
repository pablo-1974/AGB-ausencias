# config_calendar_router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Form
from starlette.responses import RedirectResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from models import SchoolCalendar
from auth import admin_required


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
    # Convertir lista de festivos separados por coma
    festivos = [
        h.strip() for h in other_holidays.split(",") if h.strip()
    ]

    # Crear nuevo registro (no editamos, creamos uno nuevo cada vez)
    cal = SchoolCalendar(
        school_year=school_year,
        first_day=first_day,
        last_day=last_day,
        xmas_start=xmas_start,
        xmas_end=xmas_end,
        easter_start=easter_start,
        easter_end=easter_end,
        other_holidays=festivos,
    )

    session.add(cal)
    await session.commit()

    # Volvemos a la página de calendario
    return RedirectResponse("/config/calendar/", status_code=303)
