# teachers_router.py
from __future__ import annotations
from datetime import date
from typing import List, Optional
import unicodedata

from fastapi import APIRouter, Depends, Request, Query
from starlette.responses import FileResponse
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher, TeacherStatus, Leave
from services.pdf_teachers import generate_teachers_list_pdf

router = APIRouter()


# ======================================================
#   Helpers plantillas
# ======================================================
def _templates(request: Request):
    tpl = getattr(request.app.state, "templates", None)
    if tpl is None:
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory="templates")
        request.app.state.templates = tpl
    return tpl


def _ctx(request: Request, **extra):
    base = {
        "request": request,
        "title": "Listado de profesorado",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# ======================================================
#   Helpers ordenación sin tildes
# ======================================================
def _normalize_name(name: str) -> str:
    """Orden alfabético: Álvarez -> alvarez"""
    nf = unicodedata.normalize("NFD", name)
    return "".join(ch for ch in nf if not unicodedata.combining(ch)).lower()


# ======================================================
#   Obtener Profes Actual / Titular / Sustituto
# ======================================================
async def _get_profesorado_actual(session: AsyncSession):
    """
    PROFESORADO ACTUAL:
      1) Activos
      2) En baja/excedencia sin sustituto → bloque aparte
    """

    # -------------------------------
    # 1) Profes activos
    # -------------------------------
    q_activos = select(Teacher).where(Teacher.status == TeacherStatus.activo)
    activos = (await session.execute(q_activos)).scalars().all()
    activos = sorted(activos, key=lambda t: _normalize_name(t.name))

    # -------------------------------
    # 2) Profes en baja/excedencia sin sustituto
    # -------------------------------
    # tenemos que buscar leaves activas y sin substitute_teacher_id
    q_bajas = (
        select(Teacher)
        .join(Leave, Leave.teacher_id == Teacher.id)
        .where(
            and_(
                Teacher.status.in_([TeacherStatus.baja, TeacherStatus.excedencia]),
                Leave.end_date.is_(None),
                or_(
                    Leave.substitute_teacher_id.is_(None),
                    Leave.substitute_teacher_id == 0,
                ),
            )
        )
    )

    ausentes_sin_sustituto = (await session.execute(q_bajas)).scalars().all()
    ausentes_sin_sustituto = sorted(ausentes_sin_sustituto, key=lambda t: _normalize_name(t.name))

    return activos, ausentes_sin_sustituto


async def _get_profesorado_titular(session: AsyncSession):
    q = select(Teacher).where(Teacher.titular == True)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: _normalize_name(t.name))


async def _get_profesorado_sustituto(session: AsyncSession):
    q = select(Teacher).where(Teacher.titular == False)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: _normalize_name(t.name))


# ======================================================
#   RUTA PRINCIPAL /teachers/list
# ======================================================
@router.get("/teachers/list")
async def teachers_list(
    request: Request,
    tipo: str = Query("actual", pattern="^(actual|titular|sustituto)$"),
    session: AsyncSession = Depends(get_session),
):

    if tipo == "actual":
        activos, ausentes_sin_sustituto = await _get_profesorado_actual(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(
                request,
                tipo="actual",
                activos=activos,
                ausentes_sin_sustituto=ausentes_sin_sustituto,
            ),
        )

    elif tipo == "titular":
        lista = await _get_profesorado_titular(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(request, tipo="titular", lista=lista),
        )

    elif tipo == "sustituto":
        lista = await _get_profesorado_sustituto(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(request, tipo="sustituto", lista=lista),
        )


# ======================================================
#   PDF
# ======================================================
@router.get("/teachers/list/pdf")
async def teachers_list_pdf(
    tipo: str = Query("actual", pattern="^(actual|titular|sustituto)$"),
    session: AsyncSession = Depends(get_session),
):

    if tipo == "actual":
        activos, ausentes = await _get_profesorado_actual(session)
        items = activos + ausentes
        title = "Profesorado Actual"

    elif tipo == "titular":
        items = await _get_profesorado_titular(session)
        title = "Profesorado Titular"

    elif tipo == "sustituto":
        items = await _get_profesorado_sustituto(session)
        title = "Profesorado Sustituto"

    # Para PDF convertimos objetos Teacher -> nombres
    items_pdf = [t.name for t in items]

    # Ordenar sin tildes
    items_pdf = sorted(items_pdf, key=_normalize_name)

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")

    generate_teachers_list_pdf(
        path=tmp.name,
        center_name=(settings.INSTITUTION_NAME or ""),
        title=title,
        items=items_pdf,
        date_str=None,
    )

    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=f"{tipo}_profesorado.pdf",
    )
