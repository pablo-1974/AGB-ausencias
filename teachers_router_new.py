# teachers_router.py
from __future__ import annotations
from datetime import date
from typing import Optional
import unicodedata

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import FileResponse
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
    return request.app.state.templates


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
    nf = unicodedata.normalize("NFD", name)
    return "".join(ch for ch in nf if not unicodedata.combining(ch)).lower()


# ======================================================
#   PROFESORADO ACTUAL: ACTIVO + BAJA SIN SUSTITUTO
# ======================================================
async def _get_profesorado_actual(session: AsyncSession):
    q_activos = select(Teacher).where(Teacher.status == TeacherStatus.activo)
    activos = (await session.execute(q_activos)).scalars().all()
    activos = sorted(activos, key=lambda t: _normalize_name(t.name))

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
    ausentes_sin_sustituto = sorted(
        ausentes_sin_sustituto, key=lambda t: _normalize_name(t.name)
    )

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
        activos, ausentes = await _get_profesorado_actual(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(
                request,
                tipo="actual",
                activos=activos,
                ausentes_sin_sustituto=ausentes,
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
#   FUNCIÓN AUXILIAR PARA GENERAR PDFs
# ======================================================
def _make_pdf(items, filename, title, center_name):
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.close()

    generate_teachers_list_pdf(
        path=tmp.name,
        center_name=center_name,
        title=title,
        items=items,
        date_str=date.today().strftime("%d/%m/%Y"),
        logo_path=settings.LOGO_PATH,
    )

    return FileResponse(tmp.name, media_type="application/pdf", filename=filename)


# ======================================================
#   MENÚ COMPLETO DE PDFS
# ======================================================
@router.get("/teachers/print/all")
async def teachers_print_all(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).order_by(Teacher.name.asc())
        )
    ).scalars().all()

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Todos.pdf", "Listado completo", settings.INSTITUTION_NAME)


@router.get("/teachers/print/activos")
async def teachers_print_activos(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher)
            .where(Teacher.status == TeacherStatus.activo)
            .order_by(Teacher.name.asc())
        )
    ).scalars().all()

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Activos.pdf", "Profesorado Activo", settings.INSTITUTION_NAME)


@router.get("/teachers/print/bajas")
async def teachers_print_bajas(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher)
            .where(Teacher.status.in_([TeacherStatus.baja, TeacherStatus.excedencia]))
            .order_by(Teacher.name.asc())
        )
    ).scalars().all()

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Bajas.pdf", "Profesores en Baja o Excedencia", settings.INSTITUTION_NAME)


@router.get("/teachers/print/exprofes")
async def teachers_print_exprofes(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher)
            .where(Teacher.status == TeacherStatus.exprofe)
            .order_by(Teacher.name.asc())
        )
    ).scalars().all()

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Exprofesorado.pdf", "Exprofesorado", settings.INSTITUTION_NAME)

