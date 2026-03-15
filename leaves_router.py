# leaves_router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import Optional

from database import get_session
from config import settings
from auth import admin_required
from models import Teacher, TeacherStatus, Leave
from services.leaves import close_leave

router = APIRouter()

def _templates(request: Request):
    return request.app.state.templates

def _ctx(request: Request, **extra):
    base = {
        "request": request,
        "title": "Bajas",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base

#### router GET /leaves/close #####
@router.get("/leaves/close")
async def leaves_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    # Profesores que TIENEN una baja/excedencia abierta (end_date NULL)
    res = await session.execute(
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(Leave.end_date.is_(None))
        .order_by(Teacher.name.asc())
    )
    rows = res.all()  # (Leave, Teacher)
    open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        _ctx(request, open_items=open_items, title="Finalizar baja"),
    )

#### router POST /leaves/finish #####
@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    next_url: str | None = Form(None, alias="next"),   # ⬅ recoge 'next' si viene del formulario
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    try:
        # Cierra la baja + revertir sustitución (estados y horario), y fija substitute_end_date
        await close_leave(session, teacher_id=teacher_id, end_date=end_date)

        # ✅ Si nos pasaron 'next', volvemos a la lista con filtros
        if next_url:
            return RedirectResponse(next_url, status_code=303)

        # ✅ Si no hay 'next', volver a Ver Bajas por defecto
        return RedirectResponse("/leaves", status_code=303)

    except Exception as e:
        # Si hay error, re-pintamos el formulario con el mensaje de error
        res = await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
            .order_by(Teacher.name.asc())
        )
        rows = res.all()
        open_items = [
            {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
            for (l, t) in rows
        ]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, open_items=open_items, error=str(e)),
            status_code=400,
        )


# --- NUEVO: Iniciar baja ---

from services.leaves import open_leave  # ya importaste close_leave arriba; añadimos open_leave
from fastapi import HTTPException

#### router GET /leaves/new #####
@router.get("/leaves/new")
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # <-- ajusta si no debe requerir admin
):
    """
    Muestra el formulario de apertura de baja con el listado de profesores.
    """
    # Cargar profesores (solo activos, filtra por status)
    q = select(Teacher).where(Teacher.status == TeacherStatus.activo).order_by(Teacher.name.asc())
    teachers = (await session.execute(q)).scalars().all()

    return _templates(request).TemplateResponse(
        "leaves_new.html",
        _ctx(request, title="Iniciar baja", teachers=teachers),
    )

#### router POST /leaves/new #####
@router.post("/leaves/new")
async def leaves_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    teacher_id: int = Form(...),
    start_date: date = Form(...),
    leave_type: str = Form("baja"),    # 'baja' | 'excedencia'
    cause: str = Form("Baja"),
    category: str = Form(...),         # ⬅⬅⬅ NUEVO (obligatorio)
):
    """
    Procesa el formulario e inicia la baja usando services.leaves.open_leave.
    """

    # ----------- Mapeo del tipo de baja -----------
    lt = TeacherStatus.baja if leave_type == "baja" else TeacherStatus.excedencia

    # ----------- VALIDACIÓN DE CATEGORÍA A–L -----------
    if category not in list("ABCDEFGHIJKL"):
        # Recargar profesores activos para repintar la plantilla
        q = select(Teacher).where(Teacher.status == TeacherStatus.activo).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()

        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(
                request,
                title="Iniciar baja",
                teachers=teachers,
                error="Debe seleccionar una categoría válida (A–L)."
            ),
            status_code=400
        )

    try:
        # ----------- CREAR LA BAJA CON CATEGORÍA -----------
        await open_leave(
            session=session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=lt,
            cause=cause or "Baja",
            category=category,    # ⬅⬅⬅ NUEVO
        )

        # ✔ Tras crear → volvemos a listado de bajas
        return RedirectResponse("/leaves", status_code=303)

    except Exception as e:
        # Recargar profesores activos para repintar plantilla con error
        q = select(Teacher).where(Teacher.status == TeacherStatus.activo).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()

        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(request, title="Iniciar baja", teachers=teachers, error=str(e)),
            status_code=400
        )

# --- SUSTITUCIONES ---
from fastapi import HTTPException
from sqlalchemy import and_, or_, exists
from services.leaves import set_substitution
from services.schedule import clone_teacher_schedule  # <-- te doy esta función más abajo

#### router GET /substitutions/new #####
@router.get("/substitutions/new")
async def substitutions_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # lo afinamos cuando hablemos de roles
):
    # Profes a sustituir: Leave abierto (end_date NULL), sin sustituto, y status baja/excedencia
    res = await session.execute(
        select(Leave, Teacher)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .where(
            and_(
                Leave.end_date.is_(None),
                Leave.substitute_teacher_id.is_(None),
                or_(Teacher.status == TeacherStatus.baja, Teacher.status == TeacherStatus.excedencia),
            )
        )
        .order_by(Teacher.name.asc())
    )
    rows = res.all()
    open_leaves = [
        {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
        for (l, t) in rows
    ]

    # Exprofes: por defecto, docentes no activos y sin baja abierta
    # (si prefieres otro criterio de "exprof", lo cambiamos)
    subq_open_leave = (
        select(Leave.id)
        .where(and_(Leave.teacher_id == Teacher.id, Leave.end_date.is_(None)))
        .limit(1)
        .scalar_subquery()
    )
    exprofes = (
        (await session.execute(
            select(Teacher)
            .where(
                and_(
                    Teacher.status == TeacherStatus.exprofe,
                    subq_open_leave.is_(None)
                )
            )
            .order_by(Teacher.name.asc())
        )).scalars().all()
    )

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        _ctx(request, title="Iniciar sustitución", open_leaves=open_leaves, exprofes=exprofes),
    )

#### router POST /substitutions/new #####
@router.post("/substitutions/new")
async def substitutions_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    teacher_id: int = Form(...),        # sustituido
    start_date: date = Form(...),
    sub_mode: str = Form(...),          # 'exprof' | 'new'
    # ARREGLO B: aceptar string opcional, tolerar "" y convertir a int con control
    exprof_teacher_id: Optional[str] = Form(None),
    # Datos de nuevo profesor
    new_name: Optional[str] = Form(None),
    new_email: Optional[str] = Form(None),
    new_alias: Optional[str] = Form(None),
):
    # Validaciones de selección
    if sub_mode not in ("exprof", "new"):
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(request, error="Debes elegir 'Exprofes' o 'Profesor nuevo'."),
            status_code=400,
        )

    # Verificar que el profesor a sustituir tiene baja/excedencia abierta y sin sustituto
    leave_row = (await session.execute(
        select(Leave)
        .where(
            and_(
                Leave.teacher_id == teacher_id,
                Leave.end_date.is_(None),
                Leave.substitute_teacher_id.is_(None),
            )
        )
    )).scalar_one_or_none()
    if not leave_row:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(request, error="El profesor seleccionado no tiene una baja/excedencia abierta sin sustituto."),
            status_code=400,
        )

    # Resolver el ID del sustituto
    substitute_teacher_id: Optional[int] = None

    if sub_mode == "exprof":
        # ARREGLO B: tolerar cadena vacía y convertir a int de forma segura
        exprof_teacher_id = (exprof_teacher_id or "").strip()
        if not exprof_teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="Debes seleccionar un exprofesor para la sustitución."),
                status_code=400,
            )
        try:
            exprof_id = int(exprof_teacher_id)
        except ValueError:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="Identificador de exprofesor no válido."),
                status_code=400,
            )

        # Activar al exprof como 'activo'
        sub_t = (await session.execute(select(Teacher).where(Teacher.id == exprof_id))).scalar_one_or_none()
        if not sub_t:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="Exprofesor no encontrado."),
                status_code=404,
            )
        if sub_t.id == teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="El sustituto no puede ser el mismo profesor sustituido."),
                status_code=400,
            )
        
        # Activar al exprof como activo → NO titular
        sub_t.status = TeacherStatus.activo
        sub_t.titular = False    # ⬅⬅⬅ NUEVO
        
        substitute_teacher_id = sub_t.id

    else:  # sub_mode == "new"
        new_name = (new_name or "").strip()
        new_email = (new_email or "").strip()
        new_alias = (new_alias or "").strip()
        if not new_name or not new_email or not new_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="Nombre, Email y Alias del nuevo profesor son obligatorios."),
                status_code=400,
            )
        # Unicidad de alias (y opcionalmente email)
        exists_alias = (await session.execute(
            select(Teacher.id).where(Teacher.alias == new_alias)
        )).scalar_one_or_none()
        if exists_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="El alias ya existe. Elige otro."),
                status_code=400,
            )
        # Crear profesor nuevo como activo
        new_t = Teacher(
            name=new_name,
            email=new_email,
            alias=new_alias,
            status=TeacherStatus.activo,
            titular=False   # ⬅⬅⬅ NUEVO
        )
        session.add(new_t)
        
        await session.flush()  # obtener ID
        substitute_teacher_id = new_t.id

    # Asignar sustitución en la baja (services.leaves.set_substitution hace el commit)
    await set_substitution(
        session=session,
        teacher_id=teacher_id,
        start_date=start_date,
        substitute_teacher_id=substitute_teacher_id,
    )

    # HEREDAR HORARIO: clona el horario del sustituido al sustituto desde start_date
    try:
        await clone_teacher_schedule(
            session,
            source_teacher_id=teacher_id,
            target_teacher_id=substitute_teacher_id,
            effective_from=start_date
        )
    except Exception as e:
        # (Puedes decidir también redirigir a /leaves con un sistema de mensajes flash)
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(request, info="Sustitución creada, pero hubo un problema heredando el horario: " + str(e)),
        )

    # ✅ Tras crear sustitución, volver a Ver Bajas
    return RedirectResponse("/leaves", status_code=303)

# VER BAJAS
from typing import Optional
from fastapi import Query
from sqlalchemy.orm import aliased

#### router GET /leaves #####
@router.get("/leaves", response_class=HTMLResponse)
async def leaves_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    status: str = Query("open", pattern="^(open|all)$"),
    with_sub: Optional[str] = Query(None),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    Sub = aliased(Teacher)

    # Base: join al titular y left join al sustituto
    q = (
        select(Leave, Teacher, Sub)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .outerjoin(Sub, Sub.id == Leave.substitute_teacher_id)
    )

    # Filtro por estado de la baja
    if status == "open":
        q = q.where(Leave.end_date.is_(None))

    # Filtro por si tiene sustituto o no (en curso o ya cerrado)
    ws = (with_sub or "").strip().lower()
    if ws == "true":
        q = q.where(Leave.substitute_teacher_id.is_not(None))
    elif ws == "false":
        q = q.where(Leave.substitute_teacher_id.is_(None))

    # Orden
    if order == "desc":
        q = q.order_by(Leave.start_date.desc(), Teacher.name.asc())
    else:
        q = q.order_by(Leave.start_date.asc(), Teacher.name.asc())

    rows = (await session.execute(q)).all()

    items = []
    for lv, t, sub in rows:
        items.append({
            "leave_id": lv.id,
            "teacher_id": t.id,                # para /leaves/finish
            "teacher_name": t.name,
            "start_date": lv.start_date,
            "cause": lv.cause or "",
            "sub_start_date": getattr(lv, "substitute_start_date", None),
            "sub_end_date": getattr(lv, "substitute_end_date", None),
            "sub_name": sub.name if sub else None,
        })

    # Pasamos los filtros actuales para poder reconstruir enlaces en la plantilla si quieres
    return _templates(request).TemplateResponse(
        "leaves_list.html",
        _ctx(
            request,
            title="Bajas (ver)",
            items=items,
            current_filters={"status": status, "with_sub": with_sub, "order": order},
        ),
    )
