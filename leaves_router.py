# ============================================================
# leaves_router.py — Gestión de bajas y sustituciones
# Con soporte de cadenas infinitas de sustitución:
# P1 → P2 → P3 → ... con un único activo en la cadena
# Lógica sincronizada con services/leaves.py (versión final)
# ============================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from sqlalchemy import select, and_, or_
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
    close_leave_cascade,
    set_substitution,
    get_substitution_chain
)

from services.schedule import clone_teacher_schedule

router = APIRouter()

def _templates(request: Request):
    return request.app.state.templates


# ============================================================
# Finalizar baja
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
        {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
        for (l,t) in sorted(rows, key=lambda r: normalize_name(r[1].name))
    ]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        ctx(request, admin, title="Finalizar baja", open_items=open_items)
    )


@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    next_url: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    try:
        await close_leave_cascade(session, teacher_id, end_date)
        return RedirectResponse(next_url or "/leaves", 303)
    except Exception as e:
        return _templates(request).TemplateResponse(
            "leaves_close.html",
            ctx(request, admin, error=str(e)),
            status_code=400,
        )


# ============================================================
# Crear baja
# ============================================================
@router.get("/leaves/new")
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required)
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
            category=category
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
            status_code=400
        )


# ============================================================
# Crear sustitución
# ============================================================
@router.get("/substitutions/new")
async def substitutions_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required)
):
    # ✅ SOLO profesores al final de la cadena
    raw = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                and_(
                    Leave.end_date.is_(None),
                    Leave.substitute_teacher_id.is_(None)   # ✅ clave
                )
            )
        )
    ).all()

    open_leaves = sorted(
        [
            {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
            for (l, t) in raw
        ],
        key=lambda x: normalize_name(x["teacher_name"]),
    )

    # Exprofes disponibles
    ex_raw = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.exprofe)
        )
    ).scalars().all()
    exprofes = sorted(ex_raw, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        ctx(request, admin, title="Iniciar sustitución", open_leaves=open_leaves, exprofes=exprofes),
    )


@router.post("/substitutions/new")
async def substitutions_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    teacher_id: int = Form(...),
    start_date: date = Form(...),
    sub_mode: str = Form(...),
    exprof_teacher_id: Optional[str] = Form(None),
    new_name: Optional[str] = Form(None),
    new_email: Optional[str] = Form(None),
    new_alias: Optional[str] = Form(None),
):
    lv = await session.scalar(
        select(Leave).where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None)
            )
        )
    )
    if not lv:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            ctx(request, admin, error="El profesor no tiene baja activa"),
            status_code=400
        )

    if sub_mode=="exprof":
        if not exprof_teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                ctx(request, admin, error="Debes seleccionar un exprofesor"),
                status_code=400
            )
        sub = await session.get(Teacher, int(exprof_teacher_id))
        if not sub:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                ctx(request, admin, error="Exprofesor no encontrado"),
                status_code=404
            )
        substitute_id = sub.id
        sub.status = TeacherStatus.activo
        sub.titular = False

    elif sub_mode=="new":
        if not new_name or not new_email or not new_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                ctx(request, admin, error="Campos obligatorios"),
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
            ctx(request, admin, error="Modo inválido"),
            status_code=400
        )

    # registrar sustitución
    await set_substitution(session, teacher_id, start_date, substitute_id)

    try:
        await clone_teacher_schedule(
            session,
            source_teacher_id=teacher_id,
            target_teacher_id=substitute_id,
            effective_from=start_date
        )
    except Exception as e:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            ctx(request, admin, info=f"Sustituto creado, pero fallo clonando horario: {e}")
        )

    return RedirectResponse("/leaves", 303)


# ============================================================
# Finalizar sustitución
# ============================================================
@router.post("/substitutions/end")
async def substitution_end_route(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    try:
        await end_substitution(session, teacher_id, end_date)
        return RedirectResponse("/leaves", 303)
    except Exception as e:
        return _templates(request).TemplateResponse(
            "leaves_list.html",
            ctx(request, admin, error=str(e)),
            status_code=400
        )


# ============================================================
# Listado de bajas
# ============================================================
@router.get("/leaves", response_class=HTMLResponse)
async def leaves_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    status: str = Query("open", pattern="^(open|all)$"),
    with_sub: Optional[str] = Query(None),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    from sqlalchemy.orm import aliased
    Sub = aliased(Teacher)

    q = (
        select(Leave, Teacher, Sub)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .outerjoin(Sub, Sub.id == Leave.substitute_teacher_id)
    )

    if status == "open":
        q = q.where(Leave.end_date.is_(None))

    if with_sub:
        if with_sub.lower() == "true":
            q = q.where(Leave.substitute_teacher_id.is_not(None))
        elif with_sub.lower() == "false":
            q = q.where(Leave.substitute_teacher_id.is_(None))

    q = q.order_by(Leave.start_date.desc() if order=="desc" else Leave.start_date.asc())

    rows = (await session.execute(q)).all()

    items = []

    for lv, t, sub in rows:
        chain_ids = await get_substitution_chain(session, t.id)
        chain_names = []
        for cid in chain_ids:
            c = await session.get(Teacher, cid)
            if c:
                chain_names.append(c.name)

        items.append({
            "leave_id": lv.id,
            "teacher_id": t.id,
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "end_date": lv.end_date,
            "cause": lv.cause or "",
            "sub_name": sub.name if sub else None,
            "sub_start_date": lv.substitute_start_date,
            "sub_end_date": lv.substitute_end_date,
            "chain": chain_names
        })

    return _templates(request).TemplateResponse(
        "leaves_list.html",
        ctx(request, admin, title="Bajas y sustituciones", items=items),
    )

# ============================================================
# 5) ADMINISTRACIÓN DE BAJAS (LISTAR / EDITAR / BORRAR)
# ============================================================

@router.get("/leaves/admin", response_class=HTMLResponse)
async def leaves_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Panel de administración: editar o borrar bajas."""
    user = admin

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
        ctx(request, user, title="Edición de bajas", items=items),
    )


@router.get("/leaves/edit/{leave_id}", response_class=HTMLResponse)
async def leaves_edit_form(
    leave_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Formulario de edición de una baja."""
    user = admin

    l = await session.get(Leave, leave_id)
    if not l:
        return RedirectResponse("/leaves/admin", 303)

    t = await session.get(Teacher, l.teacher_id)

    return _templates(request).TemplateResponse(
        "leaves_edit.html",
        ctx(
            request,
            user,
            title="Editar baja",
            leave=l,
            teacher=t,
            categories=list("ABCDEFGHIJKL"),
            current_category=(l.category or ""),
        ),
    )


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
    """Guarda cambios en una baja."""
    user = admin

    l = await session.get(Leave, leave_id)
    if not l:
        return RedirectResponse("/leaves/admin", 303)

    l.start_date = start_date
    l.end_date = end_date
    l.cause = (reason or "").strip()
    l.category = (category or "").strip()

    await session.commit()

    return RedirectResponse("/leaves/admin", 303)


@router.post("/leaves/delete/{leave_id}")
async def leaves_delete(
    leave_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Borra permanentemente una baja."""
    l = await session.get(Leave, leave_id)
    if l:
        await session.delete(l)
        await session.commit()

    return RedirectResponse("/leaves/admin", 303)
