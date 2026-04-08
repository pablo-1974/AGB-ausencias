# ============================================================
# leaves_router.py — Gestión de bajas jerárquicas y sustituciones
# Nuevo sistema con parent_leave_id en SQL
# ============================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import Optional

from database import get_session
from auth import admin_required
from models import Teacher, TeacherStatus, Leave, User
from utils import normalize_name
from context import ctx

from services.leaves import (
    open_leave,
    set_substitution,
    close_leave_cascade,
    close_leave_subtree,   # ✅ NUEVO
)

from services.schedule import clone_teacher_schedule

router = APIRouter()

def _templates(request: Request):
    return request.app.state.templates


# ============================================================
# MOSTRAR FORMULARIO PARA CERRAR BAJAS
# ============================================================
@router.get("/leaves/close")
async def leaves_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
        )
    ).all()

    open_items = [
        {
            "leave_id": l.id,
            "teacher_name": t.name,
            "start_date": l.start_date,
            "is_root": l.parent_leave_id is None,  # útil para la vista
        }
        for (l, t) in sorted(rows, key=lambda r: normalize_name(r[1].name))
    ]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        ctx(request, admin, title="Finalizar baja / sustitución", open_items=open_items)
    )


# ============================================================
# CERRAR BAJA (RAÍZ O SUSTITUTO)
# ============================================================
@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    leave_id: int = Form(...),
    end_date: date = Form(...),
    next_url: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    try:
        leave = await session.get(Leave, leave_id)
        if not leave:
            raise Exception("La baja no existe.")

        # ✅ Decisión clave según sea raíz o sustituto
        if leave.parent_leave_id is None:
            # Vuelve el titular → cerrar TODO
            await close_leave_cascade(session, leave_id, end_date)
        else:
            # Vuelve un sustituto → cerrar solo su subárbol
            await close_leave_subtree(session, leave_id, end_date)

        return RedirectResponse(next_url or "/leaves", 303)

    except Exception as e:
        return _templates(request).TemplateResponse(
            "leaves_close.html",
            ctx(request, admin, error=str(e)),
            status_code=400,
        )


# ============================================================
# CREAR BAJA RAÍZ
# ============================================================
@router.get("/leaves/new")
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):

    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()

    teachers = sorted(rows, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "leaves_new.html",
        ctx(request, admin, title="Iniciar baja", teachers=teachers)
    )


@router.post("/leaves/new")
async def leaves_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    teacher_id: int = Form(...),
    start_date: date = Form(...),
    leave_type: str = Form("baja"),
    cause: str = Form("Baja"),
    category: Optional[str] = Form(None),
):

    lt = TeacherStatus.baja if leave_type == "baja" else TeacherStatus.excedencia

    try:
        await open_leave(
            session=session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=lt,
            cause=cause,
            category=category,
            parent_leave_id=None,
        )
        return RedirectResponse("/leaves", 303)

    except Exception as e:
        rows = (
            await session.execute(
                select(Teacher).where(Teacher.status == TeacherStatus.activo)
            )
        ).scalars().all()

        teachers = sorted(rows, key=lambda t: normalize_name(t.name))

        return _templates(request).TemplateResponse(
            "leaves_new.html",
            ctx(request, admin, title="Iniciar baja", teachers=teachers, error=str(e)),
            status_code=400,
        )


# ============================================================
# CREAR SUSTITUCIÓN (baja hija)
# ============================================================
@router.get("/substitutions/new")
async def substitutions_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required)
):
    # --------------------------------------------------------
    # Solo se pueden sustituir bajas activas que NO tengan
    # ninguna baja hija activa (es decir, hojas del árbol).
    # Esto evita ofrecer bajas intermedias cuyo único sentido
    # es estar sustituyendo a otro profesor.
    # --------------------------------------------------------

    # Subquery: IDs de bajas que tienen una hija activa
    child_exists = (
        select(Leave.parent_leave_id)
        .where(Leave.end_date.is_(None))
        .subquery()
    )

    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                and_(
                    Leave.end_date.is_(None),
                    Leave.substitute_teacher_id.is_(None),
                    Leave.id.not_in(
                        select(child_exists.c.parent_leave_id)
                    )
                )
            )
        )
    ).all()

    open_leaves = [
        {
            "leave_id": l.id,
            "teacher_name": t.name,
            "start_date": l.start_date
        }
        for (l, t) in rows
    ]

    exprofes = (
        await session.execute(
            select(Teacher)
            .where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        ctx(
            request,
            admin,
            title="Iniciar sustitución",
            open_leaves=open_leaves,
            exprofes=exprofes
        ),
    )



@router.post("/substitutions/new")
async def substitutions_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    leave_id: int = Form(...),
    start_date: date = Form(...),
    sub_mode: str = Form(...),
    exprof_teacher_id: Optional[int] = Form(None),
    new_name: Optional[str] = Form(None),
    new_email: Optional[str] = Form(None),
    new_alias: Optional[str] = Form(None),
):

    leave = await session.get(Leave, leave_id)
    if not leave or leave.end_date is not None:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            ctx(request, admin, error="La baja seleccionada no está activa"),
            status_code=400
        )

    if sub_mode == "exprof":
        if not exprof_teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                ctx(request, admin, error="Debes seleccionar un exprofesor"),
                status_code=400
            )
        substitute_id = exprof_teacher_id
        sub = await session.get(Teacher, substitute_id)
        sub.status = TeacherStatus.activo
        sub.titular = False

    elif sub_mode == "new":
        if not new_name or not new_email or not new_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                ctx(request, admin, error="Todos los campos son obligatorios"),
                status_code=400
            )
        new_t = Teacher(
            name=new_name.strip(),
            email=new_email.strip(),
            alias=new_alias.strip(),
            status=TeacherStatus.activo,
            titular=False
        )
        session.add(new_t)
        await session.flush()
        substitute_id = new_t.id

    else:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            ctx(request, admin, error="Modo de sustitución inválido"),
            status_code=400
        )

    # Crear baja hija
    await set_substitution(
        session,
        teacher_id=leave.teacher_id,
        start_date=start_date,
        substitute_teacher_id=substitute_id,
    )

    # Clonar horarios
    try:
        await clone_teacher_schedule(
            session,
            source_teacher_id=leave.teacher_id,
            target_teacher_id=substitute_id,
            effective_from=start_date
        )
    except Exception as e:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            ctx(request, admin, info=f"Sustituto creado, pero fallo clonando horarios: {e}")
        )

    await session.commit()
    return RedirectResponse("/leaves", 303)


# ============================================================
# LISTADO DE BAJAS
# ============================================================
@router.get("/leaves", response_class=HTMLResponse)
async def leaves_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    status: str = Query("open", pattern="^(open|all)$"),
):

    # ✅ Solo bajas raíz
    q = (
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(Leave.parent_leave_id == None)
    )

    if status == "open":
        q = q.where(Leave.end_date.is_(None))

    rows = (await session.execute(q)).all()

    items = []

    for lv, t in rows:

        # ✅ Cadena ordenada
        children = (
            await session.execute(
                select(Leave)
                .where(Leave.parent_leave_id == lv.id)
                .order_by(Leave.start_date)
            )
        ).scalars().all()

        chain = []
        for ch in children:
            tt = await session.get(Teacher, ch.teacher_id)
            chain.append(tt.name)

        items.append({
            "leave_id": lv.id,
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "end_date": lv.end_date,
            "cause": lv.cause or "",
            "chain": chain,
        })

    return _templates(request).TemplateResponse(
        "leaves_list.html",
        ctx(request, admin, title="Bajas y sustituciones", items=items),
    )


# ============================================================
# ADMINISTRAR BAJAS
# ============================================================
@router.get("/leaves/admin", response_class=HTMLResponse)
async def leaves_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .order_by(Leave.start_date.desc(), Teacher.name.asc())
        )
    ).all()

    items = [
        {
            "id": l.id,
            "teacher_name": t.name,
            "start_date": l.start_date,
            "end_date": l.end_date,
            "reason": l.cause or "",
            "category": (l.category or "").strip(),
        }
        for (l, t) in rows
    ]

    return _templates(request).TemplateResponse(
        "leaves_admin_list.html",
        ctx(request, admin, title="Edición de bajas", items=items),
    )


# ============================================================
# EDITAR BAJA (GET)
# ============================================================
@router.get("/leaves/edit/{leave_id}", response_class=HTMLResponse)
async def leaves_edit_form(
    leave_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    l = await session.get(Leave, leave_id)
    if not l:
        return RedirectResponse("/leaves/admin", 303)

    t = await session.get(Teacher, l.teacher_id)

    return _templates(request).TemplateResponse(
        "leaves_edit.html",
        ctx(
            request,
            admin,
            title="Editar baja",
            leave=l,
            teacher=t,
            categories=list("ABCDEFGHIJKL"),
            current_category=(l.category or ""),
        ),
    )


# ============================================================
# EDITAR BAJA (POST)
# ============================================================
@router.post("/leaves/edit/{leave_id}")
async def leaves_edit_save(
    leave_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    start_date: date = Form(...),
    end_date: Optional[date] = Form(None),
    reason: str = Form(""),
    category: str = Form(""),
):
    l = await session.get(Leave, leave_id)
    if not l:
        return RedirectResponse("/leaves/admin", 303)

    l.start_date = start_date
    l.end_date = end_date
    l.cause = reason.strip()
    l.category = category.strip()

    await session.commit()

    return RedirectResponse("/leaves/admin", 303)


# ============================================================
# ELIMINAR BAJA
# ============================================================
@router.post("/leaves/delete/{leave_id}")
async def leaves_delete(
    leave_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    l = await session.get(Leave, leave_id)
    if l:
        await session.delete(l)
        await session.commit()

    return RedirectResponse("/leaves/admin", 303)
