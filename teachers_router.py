# ======================================================
# teachers_router.py — RUTAS DE PROFESORADO
# Panel informativo + administración (CRUD)
# Todas las vistas pasan ahora por el contexto global (ctx)
# para garantizar coherencia visual, fecha/hora en header,
# y evitar duplicaciones de lógica.
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
                or_(Leave.substitute_teacher_id.is_(None), Leave.substitute_teacher_id == 0),
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
# /teachers/list (ACCESO: cualquier usuario autenticado)
# ======================================================

@router.get("/teachers/list")
async def teachers_list(
    request: Request,
    tipo: str = Query("actual", pattern="^(actual|titular|sustituto)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user:
        return RedirectResponse("/login", 303)

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
# /teachers/create (GET) — Admin
# ======================================================

@router.get("/teachers/create")
async def teacher_create_form(
    request: Request,
    user: User = Depends(load_user_dep),
):
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    return _templates(request).TemplateResponse(
        "teachers_create.html",
        ctx(request, user, estados=list(TeacherStatus)),
    )


# ======================================================
# /teachers/create (POST) — Admin
# ======================================================

@router.post("/teachers/create")
async def teacher_create_save(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),

    name: str = Form(...),
    email: str = Form(""),
    alias: str = Form(""),
    status: str = Form(...),
    titular: Optional[str] = Form(None),
):
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    name = name.strip()
    email = (email or "").strip()
    alias = (alias or "").strip()

    # Validación nombre
    if not name:
        return _templates(request).TemplateResponse(
            "teachers_create.html",
            ctx(request, user, error="El nombre es obligatorio.", estados=list(TeacherStatus)),
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
                ctx(request, user, error="Ese email ya está en uso.", estados=list(TeacherStatus)),
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
                ctx(request, user, error="Ese alias ya está en uso.", estados=list(TeacherStatus)),
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

    return FileResponse(tmp.name, "application/pdf", filename)


@router.get("/teachers/print/all")
async def teachers_print_all(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Teacher))).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Todos.pdf", "Listado completo", settings.INSTITUTION_NAME)


@router.get("/teachers/print/activos")
async def teachers_print_activos(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Profesores_Activos.pdf", "Profesorado Activo", settings.INSTITUTION_NAME)


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
    return _make_pdf(items, "Profesores_Bajas.pdf", "Profesores en Baja", settings.INSTITUTION_NAME)


@router.get("/teachers/print/exprofes")
async def teachers_print_exprofes(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    items = [{"name": t.name, "email": t.email or ""} for t in rows]
    return _make_pdf(items, "Exprofesorado.pdf", "Exprofesorado", settings.INSTITUTION_NAME)


# ======================================================
# EDICIÓN DE PROFESORADO (admin)
# ======================================================

@router.get("/teachers/edit/{teacher_id}")
async def teacher_edit_form(
    teacher_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    return _templates(request).TemplateResponse(
        "teachers_edit.html",
        ctx(request, user, teacher=t, estados=list(TeacherStatus)),
    )


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
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    name = name.strip()
    email = (email or "").strip()
    alias = (alias or "").strip()

    if not name:
        return _templates(request).TemplateResponse(
            "teachers_edit.html",
            ctx(request, user, teacher=t, error="El nombre es obligatorio.", estados=list(TeacherStatus)),
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
                ctx(request, user, teacher=t, error="Ese email ya está en uso.", estados=list(TeacherStatus)),
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
                ctx(request, user, teacher=t, error="Ese alias ya está en uso.", estados=list(TeacherStatus)),
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
# ======================================================

@router.post("/teachers/delete/{teacher_id}")
async def teacher_delete(
    teacher_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    t = await session.get(Teacher, teacher_id)
    if not t:
        return RedirectResponse("/teachers/admin", 303)

    t.status = TeacherStatus.exprofe
    await session.commit()

    return RedirectResponse("/teachers/admin", 303)


# ======================================================
# PANEL ADMIN DE PROFESORADO
# ======================================================

@router.get("/teachers/admin")
async def teachers_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
):
    if not user or user.role.name != "admin":
        return RedirectResponse("/login", 303)

    rows = (await session.execute(select(Teacher))).scalars().all()
    rows = sorted(rows, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "teachers_admin_list.html",
        ctx(request, user, lista=rows, title="Edición del profesorado"),
    )
