# ======================================================
# teachers_router.py — RUTAS DE PROFESORADO
#
# REGLAS DE SEGURIDAD APLICADAS:
#  - Nadie anónimo puede acceder a ninguna ruta
#  - user  = Jefe de Estudios (gestión / consulta)
#  - admin = Administrador (acciones estructurales)
#  - No se usan comprobaciones manuales de rol en el cuerpo
# ======================================================

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
from auth import admin_required

# Ordenación sin tildes
from utils import normalize_name

# Contexto global unificado
from context import ctx

router = APIRouter()


# ======================================================
# Helpers de plantillas
# ======================================================
def _templates(request: Request):
    """Devuelve el motor de plantillas (Jinja2)."""
    return request.app.state.templates


# ======================================================
# PROFESORADO ACTUAL / TITULAR / SUSTITUTO
# ======================================================
async def _get_profesorado_actual(session: AsyncSession):
    """Devuelve activos + bajas sin sustituto."""
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
                Leave.is_substitution.is_(False),
                Leave.substitute_teacher_id.is_(None),
            )
        )
    )
    
    ausentes = (await session.execute(q_bajas)).scalars().all()
    ausentes = sorted(ausentes, key=lambda t: normalize_name(t.name))

    return activos, ausentes


async def _get_profesorado_titular(session: AsyncSession):
    """Devuelve solo titulares."""
    q = select(Teacher).where(Teacher.titular == True)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: normalize_name(t.name))


async def _get_profesorado_sustituto(session: AsyncSession):
    """Devuelve solo sustitutos."""
    q = select(Teacher).where(Teacher.titular == False)
    items = (await session.execute(q)).scalars().all()
    return sorted(items, key=lambda t: normalize_name(t.name))


# ======================================================
# CONSULTA DE PROFESORADO
# /teachers/list 
# ACCESO: user (JE + admin)
# ======================================================

@router.get("/teachers/list")
async def teachers_list(
    request: Request,
    tipo: str = Query("actual", pattern="^(actual|titular|sustituto)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):

    if tipo == "actual":
        activos, ausentes = await _get_profesorado_actual(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            ctx(request, user, tipo="actual", activos=activos, ausentes_sin_sustituto=ausentes),
        )

    if tipo == "titular":
        lista = await _get_profesorado_titular(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            ctx(request, user, tipo="titular", lista=lista),
        )

    if tipo == "sustituto":
        lista = await _get_profesorado_sustituto(session)
        return _templates(request).TemplateResponse(
            "teachers_list.html",
            ctx(request, user, tipo="sustituto", lista=lista),
        )


# ======================================================
# ALTA DE PROFESORADO
# /teachers/create (GET)
# ACCESO: admin
# ======================================================

@router.get("/teachers/create")
async def teacher_create_form(
    request: Request,
    admin: User = Depends(admin_required),
):

    return _templates(request).TemplateResponse(
        "teachers_create.html",
        ctx(request, admin, estados=list(TeacherStatus)),
    )


# ======================================================
# ALTA DE PROFESORADO
# /teachers/create (POST)
# ACCESO: admin
# ======================================================

@router.post("/teachers/create")
async def teacher_create_save(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    name: str = Form(...),
    email: str = Form(""),
    alias: str = Form(""),
    status: str = Form(...),
    titular: Optional[str] = Form(None),
):
    name = name.strip()
    email = (email or "").strip()
    alias = (alias or "").strip()

    # Validación nombre
    if not name:
        return _templates(request).TemplateResponse(
            "teachers_create.html",
            ctx(request, admin, error="El nombre es obligatorio.", estados=list(TeacherStatus)),
            400,
        )

    # Validación email único
    if email:
        dupe = (
            await session.execute(select(Teacher).where(Teacher.email == email))
        ).scalar_one_or_none()
        if dupe:
            return _templates(request).TemplateResponse(
                "teachers_create.html",
                ctx(request, admin, error="Ese email ya está en uso.", estados=list(TeacherStatus)),
                400,
            )

    # Validación alias único
    if alias:
        dupe = (
            await session.execute(select(Teacher).where(Teacher.alias == alias))
        ).scalar_one_or_none()
        if dupe:
            return _templates(request).TemplateResponse(
                "teachers_create.html",
                ctx(request, admin, error="Ese alias ya está en uso.", estados=list(TeacherStatus)),
                400,
            )

    new_t = Teacher(
        name=name,
        email=email or None,
        alias=alias or None,
        status=TeacherStatus[status],
        titular=bool(titular),
    )
    session.add(new_t)
    await session.commit()

    return RedirectResponse("/teachers/admin", 303)


# ======================================================
# GENERACIÓN DE PDFs
# ======================================================

def _make_pdf(items, filename, title, center_name):
    """Genera temporalmente un PDF y lo envía como FileResponse."""
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

    return FileResponse(
        tmp.name,
        media_type="application/pdf",
        filename=filename,
    )

# ======================================================
# /teachers/print/all (GET)
# ACCESO: user
# ======================================================
@router.get("/teachers/print/all")
async def teachers_print_all(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    rows = sorted(
        (await session.execute(select(Teacher))).scalars().all(),
        key=lambda t: normalize_name(t.name)
    )
    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Todos.pdf", "Listado completo", settings.INSTITUTION_NAME)

# ======================================================
# /teachers/print/activos (GET)
# ACCESO: user
# ======================================================
@router.get("/teachers/print/activos")
async def teachers_print_activos(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Activos.pdf", "Profesorado Activo", settings.INSTITUTION_NAME)

# ======================================================
# /teachers/print/titulares (GET)
# ACCESO: user
# ======================================================
@router.get("/teachers/print/titulares")
async def teachers_print_titulares(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.titular == True)
        )
    ).scalars().all()

    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]

    return _make_pdf(
        items,
        "Profesores_Titulares_Inicio_Curso.pdf",
        "Profesorado titular (inicio de curso)",
        settings.INSTITUTION_NAME,
    )
    
# ======================================================
# /teachers/print/bajas (GET)
# ACCESO: user
# ======================================================
@router.get("/teachers/print/bajas")
async def teachers_print_bajas(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    rows = (
        await session.execute(
            select(Teacher).where(
                Teacher.status.in_([TeacherStatus.baja, TeacherStatus.excedencia])
            )
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Bajas.pdf", "Profesores en Baja", settings.INSTITUTION_NAME)

# ======================================================
# /teachers/print/exprofes (GET)
# ACCESO: user
# ======================================================
@router.get("/teachers/print/exprofes")
async def teachers_print_exprofes(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Exprofesorado.pdf", "Exprofesorado", settings.INSTITUTION_NAME)


# ======================================================
# EDICIÓN DE PROFESORADO
# ACCESO: admin
# ======================================================

# ======================================================
# /teachers/edit (GET)
# ======================================================
@router.get("/teachers/edit/{teacher_id}")
async def teacher_edit_form(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    return _templates(request).TemplateResponse(
        "teachers_edit.html",
        ctx(request, admin, teacher=t, estados=list(TeacherStatus)),
    )

# ======================================================
# /teachers/edit (POST)
# ======================================================
@router.post("/teachers/edit/{teacher_id}")
async def teacher_edit_save(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    name: str = Form(...),
    email: str = Form(""),
    alias: str = Form(""),
    status: str = Form(...),
    titular: Optional[str] = Form(None),
):
    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    name = name.strip()
    email = (email or "").strip()
    alias = (alias or "").strip()

    if not name:
        return _templates(request).TemplateResponse(
            "teachers_edit.html",
            ctx(request, admin, teacher=t, error="El nombre es obligatorio.", estados=list(TeacherStatus)),
            400,
        )

    # Email único
    if email:
        dupe = (
            await session.execute(select(Teacher).where(Teacher.email == email, Teacher.id != t.id))
        ).scalar_one_or_none()
        if dupe:
            return _templates(request).TemplateResponse(
                "teachers_edit.html",
                ctx(request, admin, teacher=t, error="Ese email ya está en uso.", estados=list(TeacherStatus)),
                400,
            )

    # Alias único
    if alias:
        dupe = (
            await session.execute(select(Teacher).where(Teacher.alias == alias, Teacher.id != t.id))
        ).scalar_one_or_none()
        if dupe:
            return _templates(request).TemplateResponse(
                "teachers_edit.html",
                ctx(request, admin, teacher=t, error="Ese alias ya está en uso.", estados=list(TeacherStatus)),
                400,
            )

    t.name = name
    t.email = email or None
    t.alias = alias or None
    t.status = TeacherStatus[status]
    t.titular = bool(titular)

    await session.commit()
    return RedirectResponse("/teachers/admin", 303)


# ======================================================
# BORRADO SEGURO DE PROFESORADO
# /teachers/delete (POST)
# ACCESO: admin
# ======================================================

@router.post("/teachers/delete/{teacher_id}")
async def teacher_delete(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    t.status = TeacherStatus.exprofe
    await session.commit()
    return RedirectResponse("/teachers/admin", 303)


# ======================================================
# PANEL ADMIN DE PROFESORADO
# /teachers/admin
# ACCESO: admin
# ======================================================

@router.get("/teachers/admin")
async def teachers_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    rows = sorted(
        (await session.execute(select(Teacher))).scalars().all(),
        key=lambda t: normalize_name(t.name)
    )

    return _templates(request).TemplateResponse(
        "teachers_admin_list.html",
        ctx(request, admin, lista=rows, title="Edición del profesorado"),
    )
