# ======================================================
# stats_router.py — ESTADÍSTICAS DE BAJAS
# ======================================================
# Permite filtrar y ver estadísticas de bajas por:
#   - rango de fechas
#   - profesor
#   - categoría
#   - estado (abiertas / cerradas / todas)
#
# Usa el contexto global ctx() para unificar fecha, hora
# y datos comunes en la cabecera.
# ======================================================

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Query
from starlette.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import date

from database import get_session
from models import Leave, Teacher, User, SchoolCalendar
from app import load_user_dep

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter(prefix="/stats", tags=["stats"])


# ======================================================
# Helper de plantillas
# ======================================================
def _templates(request: Request):
    return request.app.state.templates


# ======================================================
# GET /stats — pantalla principal
# ======================================================
@router.get("/")
async def stats_main(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),

    # FILTROS
    d_from: date | None = Query(None, alias="from"),
    d_to: date | None = Query(None, alias="to"),
    teacher_id: int | None = Query(None),
    category: str | None = Query(None),
    status: str | None = Query(None, pattern="^(open|closed|all)$"),
):
    """Vista principal de estadísticas de bajas."""
    if not user:
        return RedirectResponse("/login", 303)

    # ------------------------------------
    # 1) Primer día del calendario escolar
    # ------------------------------------
    cal = (
        await session.execute(
            select(SchoolCalendar).order_by(SchoolCalendar.id.desc())
        )
    ).scalar_one_or_none()

    today = date.today()
    date_from = d_from or (cal.first_day if cal else today.replace(month=1, day=1))
    date_to = d_to or today

    # ------------------------------------
    # 2) Construcción dinámica de filtros
    # ------------------------------------
    conditions = [
        Leave.start_date >= date_from,
        Leave.start_date <= date_to,
    ]

    if teacher_id:
        conditions.append(Leave.teacher_id == teacher_id)

    if category:
        conditions.append(Leave.category == category)

    if status == "open":
        conditions.append(Leave.end_date.is_(None))
    elif status == "closed":
        conditions.append(Leave.end_date.is_not(None))

    # ------------------------------------
    # 3) Consulta de bajas + profesor
    # ------------------------------------
    q = (
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(and_(*conditions))
        .order_by(Leave.start_date)
    )

    rows = (await session.execute(q)).all()

    # ------------------------------------
    # 4) Formateo de datos para plantilla
    # ------------------------------------
    items = []
    for lv, t in rows:
        items.append({
            "teacher_id": t.id,
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "end_date": lv.end_date,
            "cause": lv.cause or "",
            "category": lv.category or "",
            "days": (lv.end_date - lv.start_date).days + 1 if lv.end_date else None,
        })

    # ------------------------------------
    # 5) Datos auxiliares (para filtros)
    # ------------------------------------
    teachers = (
        (await session.execute(select(Teacher).order_by(Teacher.name)))
        .scalars()
        .all()
    )

    categories = ["A", "B", "C", "D", "E", "F", "G", "H",
                  "I", "J", "K", "L", "Z"]

    # ------------------------------------
    # 6) Render plantilla
    # ------------------------------------
    return _templates(request).TemplateResponse(
        "stats_main.html",
        ctx(
            request,
            user,
            title="Estadísticas de bajas",
            items=items,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            teachers=teachers,
            categories=categories,
            selected_teacher=teacher_id,
            selected_category=category or "",
            selected_status=status or "all",
        ),
    )
