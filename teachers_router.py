# teachers_router.py
from __future__ import annotations
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query, Form
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher, TeacherStatus, Leave, User
from services.pdf_teachers import generate_teachers_list_pdf
from app import load_user_dep

# 🔥 ORDEN ESPAÑOL
from utils import normalize_name

router = APIRouter()

# ======================================================
#   Helpers plantillas
# ======================================================
def _templates(request: Request):
    return request.app.state.templates

def _ctx(request: Request, user: User, **extra):
    """
    Helper uniforme: incluye SIEMPRE el usuario autenticado.
    """
    base = {
        "request": request,
        "user": user,
        "title": "Listado de profesorado",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# ======================================================
#   PROFESORADO ACTUAL: ACTIVO + BAJA SIN SUSTITUTO
# ======================================================
async def _get_profesorado_actual(session: AsyncSession):
    q_activos = select(Teacher).where(Teacher.status == TeacherStatus.activo)
    activos = (await session.execute(q_activos)).scalars().all()
    activos = sorted(activos, key=lambda t: normalize_name(t.name))

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
        ausentes_sin_sustituto, key=lambda t: normalize_name(t.name)
    )

    return activos, ausentes_sin_sustituto


async def _get_profesorado_titular(session: AsyncSession):
    q = select(Teacher).where(Teacher.titular == True)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: normalize_name(t.name))


async def _get_profesorado_sustituto(session: AsyncSession):
    q = select(Teacher).where(Teacher.titular == False)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: normalize_name(t.name))


# ======================================================
#   RUTA PRINCIPAL /teachers/list
# ======================================================
@router.get("/teachers/list")
async def teachers_list(
    request: Request,
    tipo: str = Query("actual", pattern="^(actual|titular|sustituto)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    if tipo == "actual":
        activos, ausentes = await _get_profesorado_actual(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(
                request,
                user=user,
                tipo="actual",
                activos=activos,
                ausentes_sin_sustituto=ausentes,
            ),
        )

    elif tipo == "titular":
        lista = await _get_profesorado_titular(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(request, user=user, tipo="titular", lista=lista),
        )

    elif tipo == "sustituto":
        lista = await _get_profesorado_sustituto(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            _ctx(request, user=user, tipo="sustituto", lista=lista),
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
    rows = (await session.execute(select(Teacher))).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(
        items,
        "Profesores_Todos.pdf",
        "Listado completo",
        settings.INSTITUTION_NAME,
    )


@router.get("/teachers/print/activos")
async def teachers_print_activos(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(
        items,
        "Profesores_Activos.pdf",
        "Profesorado Activo",
        settings.INSTITUTION_NAME,
    )


@router.get("/teachers/print/bajas")
async def teachers_print_bajas(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).where(
                Teacher.status.in_([TeacherStatus.baja, TeacherStatus.excedencia])
            )
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(
        items,
        "Profesores_Bajas.pdf",
        "Profesores en Baja o Excedencia",
        settings.INSTITUTION_NAME,
    )


@router.get("/teachers/print/exprofes")
async def teachers_print_exprofes(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(
        items,
        "Exprofesorado.pdf",
        "Exprofesorado",
        settings.INSTITUTION_NAME,
    )


# ======================================================
#   NUEVO: EDITAR PROFESOR — GET
# ======================================================
@router.get("/teachers/edit/{teacher_id}")
async def teacher_edit_form(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/list", status_code=303)

    return _templates(request).TemplateResponse(
        "teachers_edit.html",
        _ctx(request, user=user, teacher=t, estados=list(TeacherStatus)),
    )


# ======================================================
#   NUEVO: EDITAR PROFESOR — POST
# ======================================================
@router.post("/teachers/edit/{teacher_id}")
async def teacher_edit_save(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),

    name: str = Form(...),
    email: str = Form(""),
    alias: str = Form(""),
    status: str = Form(...),
    titular: Optional[str] = Form(None),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/list", status_code=303)

    name = name.strip()
    email = (email or "").strip()
    alias = (alias or "").strip()

    if not name:
        return _templates(request).TemplateResponse(
            "teachers_edit.html",
            _ctx(request, user=user, teacher=t,
                 error="El nombre es obligatorio.",
                 estados=list(TeacherStatus)),
            status_code=400,
        )

    # EMAIL ÚNICO
    if email:
        dupe_email = (
            await session.execute(
                select(Teacher)
                .where(Teacher.email == email, Teacher.id != t.id)
            )
        ).scalar_one_or_none()
        if dupe_email:
            return _templates(request).TemplateResponse(
                "teachers_edit.html",
                _ctx(request, user=user, teacher=t,
                     error="Ese email ya está en uso.",
                     estados=list(TeacherStatus)),
                status_code=400,
            )

    # ALIAS ÚNICO
    if alias:
        dupe_alias = (
            await session.execute(
                select(Teacher)
                .where(Teacher.alias == alias, Teacher.id != t.id)
            )
        ).scalar_one_or_none()
        if dupe_alias:
            return _templates(request).TemplateResponse(
                "teachers_edit.html",
                _ctx(request, user=user, teacher=t,
                     error="Ese alias ya está en uso.",
                     estados=list(TeacherStatus)),
                status_code=400,
            )

    # Aplicar cambios
    t.name = name
    t.email = email or None
    t.alias = alias or None
    t.status = TeacherStatus[status]
    t.titular = bool(titular)

    await session.commit()

    return RedirectResponse("/teachers/list", status_code=303)


# ======================================================
#   NUEVO: ELIMINAR (SEGURO)
#   NO se borra: se marca como EXPROFE
# ======================================================
@router.post("/teachers/delete/{teacher_id}")
async def teacher_delete(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/list", status_code=303)

    # Eliminación segura → marcar como exprofe
    t.status = TeacherStatus.exprofe
    await session.commit()

    return RedirectResponse("/teachers/list", status_code=303)
