# ============================================================
# leaves_router.py — Gestión de bajas jerárquicas y sustituciones
# ============================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Form, Query
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
    close_leave_subtree,
)

from services.schedule import clone_teacher_schedule

from absences_router import ABSENCE_CATEGORIES

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


# ============================================================
# FORMULARIO PARA CERRAR BAJAS
# ============================================================

@router.get("/leaves/close", response_class=HTMLResponse)
async def leaves_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                Leave.end_date.is_(None),
                Leave.is_substitution.is_(False),  # ✅ solo bajas reales
            )
        )
    ).all()

    open_items = [
        {
            "leave_id": l.id,
            "teacher_name": t.name,
            "start_date": l.start_date,
            "is_root": l.parent_leave_id is None,
        }
        for (l, t) in sorted(rows, key=lambda r: normalize_name(r[1].name))
    ]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        ctx(request, admin, title="Finalizar baja / sustitución", open_items=open_items),
    )


@router.post("/leaves/finish")
async def leaves_finish(
    leave_id: int = Form(...),
    end_date: date = Form(...),
    next_url: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    leave = await session.get(Leave, leave_id)
    if not leave:
        return RedirectResponse("/leaves/close", 303)

    if leave.parent_leave_id is None:
        await close_leave_cascade(session, leave_id, end_date)
    else:
        await close_leave_subtree(session, leave_id, end_date)

    return RedirectResponse(next_url or "/leaves", 303)


# ============================================================
# CREAR BAJA RAÍZ
# ============================================================

@router.get("/leaves/new", response_class=HTMLResponse)
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    teachers = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()

    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "leaves_new.html",
        ctx(request, admin, title="Iniciar baja", teachers=teachers),
    )


@router.post("/leaves/new")
async def leaves_new_create(
    teacher_id: int = Form(...),
    start_date: date = Form(...),
    leave_type: str = Form("baja"),
    cause: str = Form(""),
    category: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    lt = (
        TeacherStatus.excedencia
        if leave_type == "excedencia"
        else TeacherStatus.baja
    )

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


# ============================================================
# CREAR SUSTITUCIÓN
# ============================================================
# Se permiten sustituciones sobre cualquier baja administrativa real activa,
# sea raíz o hija. Los leaves técnicos (is_substitution = true) no son sustituibles.

@router.get("/substitutions/new", response_class=HTMLResponse)
async def substitutions_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    child_exists = (
        select(Leave.parent_leave_id)
        .where(
            and_(
                Leave.end_date.is_(None),
                Leave.parent_leave_id.is_not(None),
            )
        )
        .subquery()
    )

    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                and_(
                    Leave.is_substitution.is_(False),         # ✅ baja administrativa real
                    Leave.end_date.is_(None),                 # ✅ activa
                    Leave.substitute_teacher_id.is_(None),    # ✅ sin sustituto directo
                    Leave.id.not_in(select(child_exists.c.parent_leave_id)),
                )
            )

            .order_by(Leave.start_date.asc())
        )
    ).all()

    open_leaves = [
        {"leave_id": l.id, "teacher_name": t.name, "start_date": l.start_date}
        for (l, t) in rows
    ]

    exprofes = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        ctx(
            request,
            admin,
            title="Iniciar sustitución",
            open_leaves=open_leaves,
            exprofes=exprofes,
        ),
    )


@router.post("/substitutions/new")
async def substitutions_new_create(
    leave_id: int = Form(...),
    start_date: date = Form(...),
    sub_mode: str = Form(...),
    exprof_teacher_id: Optional[int] = Form(None),
    new_name: Optional[str] = Form(None),
    new_email: Optional[str] = Form(None),
    new_alias: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    leave = await session.get(Leave, leave_id)
    if not leave or leave.end_date is not None:
        return RedirectResponse("/substitutions/new", 303)

    if sub_mode == "exprof":
        substitute_id = exprof_teacher_id
        t = await session.get(Teacher, substitute_id)
        t.status = TeacherStatus.activo
        t.titular = False

    elif sub_mode == "new":
        t = Teacher(
            name=new_name.strip(),
            email=new_email.strip(),
            alias=new_alias.strip(),
            status=TeacherStatus.activo,
            titular=False,
        )
        session.add(t)
        await session.flush()
        substitute_id = t.id

    else:
        return RedirectResponse("/substitutions/new", 303)

    await set_substitution(
        session=session,
        parent_leave_id=leave.id,
        start_date=start_date,
        substitute_teacher_id=substitute_id,
    )

    await clone_teacher_schedule(
        session,
        source_teacher_id=leave.teacher_id,
        target_teacher_id=substitute_id,
        effective_from=start_date,
    )

    await session.commit()
    return RedirectResponse("/leaves", 303)


# ============================================================
# CATALOGAR BAJAS (solo detección y acceso)
# ============================================================

@router.get("/leaves/categorize", response_class=HTMLResponse)
async def leaves_categorize(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = admin

    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                and_(
                    Leave.is_substitution.is_(False),  # ✅ solo bajas reales
                    Leave.category.is_(None),          # ✅ NO catalogadas
                )
            )
            .order_by(Leave.start_date.asc(), Teacher.name.asc())
        )
    ).all()

    items = [
        {
            "id": l.id,
            "teacher_name": t.name,
            "start_date": l.start_date,
            "end_date": l.end_date,
            "cause": l.cause or "",
        }
        for (l, t) in rows
    ]

    return _templates(request).TemplateResponse(
        "leaves_categorize.html",
        ctx(
            request,
            user,
            title="Catalogar bajas",
            items=items,
            info=None if items else "No hay bajas sin catalogar.",
        ),
    )


# ============================================================
# LISTADO GENERAL
# ============================================================

@router.get("/leaves", response_class=HTMLResponse)
async def leaves_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    q = (
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(Leave.is_substitution.is_(False))
    )

    rows = (await session.execute(q)).all()

    active_items = []
    closed_items = []

    for lv, t in rows:
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

        item = {
            "leave_id": lv.id,
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "end_date": lv.end_date,
            "cause": lv.cause or "",
            "chain": chain,
        }

        if lv.end_date is None:
            active_items.append(item)
        else:
            closed_items.append(item)

    return _templates(request).TemplateResponse(
        "leaves_list.html",
        ctx(
            request,
            admin,
            title="Bajas y sustituciones",
            active_items=active_items,
            closed_items=closed_items,
        ),
    )


# ============================================================
# ADMINISTRACIÓN DE BAJAS
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


@router.get("/leaves/edit/{leave_id}", response_class=HTMLResponse)
async def leaves_edit_form(
    leave_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    leave = await session.get(Leave, leave_id)
    if not leave:
        return RedirectResponse("/leaves/admin", 303)

    teacher = await session.get(Teacher, leave.teacher_id)

    return _templates(request).TemplateResponse(
        "leaves_edit.html",
        ctx(
            request,
            admin,
            title="Editar baja",
            leave=leave,
            teacher=teacher,
            categories=ABSENCE_CATEGORIES,
            current_category=(leave.category or ""),
        ),
    )


@router.post("/leaves/edit/{leave_id}")
async def leaves_edit_save(
    leave_id: int,
    start_date: date = Form(...),
    end_date: Optional[str] = Form(None),
    reason: str = Form(""),
    category: str = Form(""),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    leave = await session.get(Leave, leave_id)
    if not leave:
        return RedirectResponse("/leaves/admin", 303)

    leave.start_date = start_date
    leave.end_date = end_date
    leave.cause = reason.strip()
    leave.category = category.strip()

    await session.commit()
    return RedirectResponse("/leaves/admin", 303)


@router.post("/leaves/delete/{leave_id}")
async def leaves_delete(
    leave_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    leave = await session.get(Leave, leave_id)
    if leave:
        await session.delete(leave)
        await session.commit()

    return RedirectResponse("/leaves/admin", 303)
