# stats_router.py
from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import date

from database import get_session
from config import settings
from models import Leave, Teacher, TeacherStatus
from app import load_user_dep   # usuario actual (no admin_required)

router = APIRouter(prefix="/stats", tags=["stats"])


def _templates(request: Request):
    return request.app.state.templates


def _ctx(request: Request, user, **extra):
    from datetime import datetime
    now = datetime.now()
    base = {
        "request": request,
        "user": user,
        "title": "Estadísticas",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
        "now": now,
    }
    base.update(extra or {})
    return base


@router.get("/")
async def stats_main(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    # --- OBTENER FECHAS INICIALES ---

    # Último calendario escolar (si existe)
    from sqlalchemy import select
    from models import SchoolCalendar

    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    if cal:
        date_from = cal.first_day       # inicio recomendado
    else:
        date_from = date(date.today().year, 1, 1)  # fallback seguro

    date_to = date.today()

    # --- CARGAR BAJAS PARA EL PERIODO ---

    q = (
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(and_(Leave.start_date >= date_from, Leave.start_date <= date_to))
        .order_by(Leave.start_date)
    )

    rows = (await session.execute(q)).all()

    items = []
    for lv, t in rows:
        items.append({
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "end_date": lv.end_date,
            "cause": lv.cause or "",
            "category": lv.category or "",
        })

    return _templates(request).TemplateResponse(
        "stats_main.html",
        _ctx(
            request,
            user=user,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            items=items,
        )
    )
