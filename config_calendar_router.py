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
