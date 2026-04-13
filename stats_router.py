# ======================================================
# stats_router.py — ESTADÍSTICAS DE BAJAS
# ======================================================
# stats_router.py — ESTADÍSTICAS (Recuento y Ranking)

from __future__ import annotations

from fastapi import APIRouter, Request, Depends, Query
from starlette.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from database import get_session
from models import Teacher, User
from app import load_user_dep
from context import ctx

from services.stats_recount import get_stats_recount
from services.stats_ranking import get_stats_ranking

router = APIRouter(prefix="/stats", tags=["stats"])


def _templates(request: Request):
    return request.app.state.templates


# ======================================================
# GET /stats/recount — RECUENTO ADMINISTRATIVO
# ======================================================
@router.get("/recount")
async def stats_recount(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),

    date_from: date = Query(...),
    date_to: date = Query(...),
    teacher_id: int | None = Query(None),
    tipo: str = Query("both", pattern="^(absences|leaves|both)$"),
    categoria: str = Query("ALL"),
):
    if not user:
        return RedirectResponse("/login", 303)

    # Llamada al servicio
    rows = await get_stats_recount(
        session,
        date_from=date_from,
        date_to=date_to,
        teacher_id=teacher_id,
        tipo=tipo,
        categoria=categoria,
    )

    # Profesores para el filtro
    teachers = (
        (await session.execute(
            Teacher.__table__.select().order_by(Teacher.name)
        ))
        .scalars()
        .all()
    )

    categorias = ["A", "B", "C", "D", "E", "F", "G",
                  "H", "I", "J", "K", "L"]

    return _templates(request).TemplateResponse(
        "stats_recount.html",
        ctx(
            request,
            user,
            title="Estadísticas · Recuento",
            rows=rows,
            teachers=teachers,
            categorias=categorias,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            teacher_id=str(teacher_id or ""),
            tipo=tipo,
            categoria=categoria,
        )
    )


# ======================================================
# GET /stats/ranking — RANKING ANALÍTICO
# ======================================================
@router.get("/ranking")
async def stats_ranking(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),

    date_from: date = Query(...),
    date_to: date = Query(...),
    tipo: str = Query("both", pattern="^(absences|leaves|both)$"),
):
    if not user:
        return RedirectResponse("/login", 303)

    rows = await get_stats_ranking(
        session,
        date_from=date_from,
        date_to=date_to,
        tipo=tipo,
    )

    return _templates(request).TemplateResponse(
        "stats_ranking.html",
        ctx(
            request,
            user,
            title="Estadísticas · Ranking",
            rows=rows,
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            tipo=tipo,
        )
    )
