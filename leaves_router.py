# leaves_router.py
from __future__ import annotations
from fastapi import APIRouter, Depends, Request, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date

from database import get_session
from config import settings
from auth import admin_required
from models import Teacher, TeacherStatus, Leave
from services.leaves import close_leave

router = APIRouter()

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
        "title": "Bajas",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base

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

@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
):
    try:
        await close_leave(session, teacher_id=teacher_id, end_date=end_date)
        # Recargar la lista para que desaparezca la que acabas de cerrar
        res = await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
            .order_by(Teacher.name.asc())
        )
        rows = res.all()
        open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, open_items=open_items, info="Baja finalizada correctamente."),
        )
    except Exception as e:
        # Volver a pintar el form con el error
        res = await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
            .order_by(Teacher.name.asc())
        )
        rows = res.all()
        open_items = [{"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date} for (l, t) in rows]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, open_items=open_items, error=str(e)),
            status_code=400,
        )

# --- NUEVO: Iniciar baja ---

from services.leaves import open_leave  # ya importaste close_leave arriba; añadimos open_leave
from fastapi import HTTPException

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

@router.post("/leaves/new")
async def leaves_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),  # <-- ajusta si no debe requerir admin
    teacher_id: int = Form(...),
    start_date: date = Form(...),
    leave_type: str = Form("baja"),  # por si luego añades selector ('baja' | 'excedencia')
    cause: str = Form("Baja"),
):
    """
    Procesa el formulario e inicia la baja usando services.leaves.open_leave.
    """
    # Mapear leave_type -> Enum
    lt = TeacherStatus.baja if leave_type == "baja" else TeacherStatus.excedencia

    try:
        await open_leave(
            session=session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=lt,
            cause=cause or "Baja",
        )
        # Redirige a una vista tras crear. Si aún no tienes listado, puedes volver al close o al dashboard.
        # Si crearás /leaves (listado), cámbialo a "/leaves".
        return RedirectResponse("/leaves/close", status_code=303)
    except HTTPException as he:
        # Re-pintar el form con error de validación del servicio
        q = select(Teacher).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()
        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(request, title="Iniciar baja", teachers=teachers, error=he.detail),
            status_code=he.status_code,
        )
    except Exception as e:
        # Error genérico
        q = select(Teacher).order_by(Teacher.name.asc())
        teachers = (await session.execute(q)).scalars().all()
        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(request, title="Iniciar baja", teachers=teachers, error=str(e)),
            status_code=400,
        )

# --- SUSTITUCIONES ---
from fastapi import HTTPException
from sqlalchemy import and_, or_, exists
from services.leaves import set_substitution
from services.schedule import clone_teacher_schedule  # <-- te doy esta función más abajo

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
                    Teacher.status != TeacherStatus.activo,
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

@router.post("/substitutions/new")
async def substitutions_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    teacher_id: int = Form(...),        # sustituido
    start_date: date = Form(...),
    sub_mode: str = Form(...),          # 'exprof' | 'new'
    exprof_teacher_id: int | None = Form(None),
    new_name: str | None = Form(None),
    new_email: str | None = Form(None),
    new_alias: str | None = Form(None),
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
    substitute_teacher_id: int | None = None

    if sub_mode == "exprof":
        if not exprof_teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, error="Debes seleccionar un exprofesor para la sustitución."),
                status_code=400,
            )
        # Activar al exprof como 'activo'
        sub_t = (await session.execute(select(Teacher).where(Teacher.id == exprof_teacher_id))).scalar_one_or_none()
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
        sub_t.status = TeacherStatus.activo
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
        new_t = Teacher(name=new_name, email=new_email, alias=new_alias, status=TeacherStatus.activo)
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
        await clone_teacher_schedule(session, source_teacher_id=teacher_id, target_teacher_id=substitute_teacher_id, effective_from=start_date)
    except Exception as e:
        # No impedimos la sustitución por fallo de horario, pero informamos
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(request, info="Sustitución creada, pero hubo un problema heredando el horario: " + str(e)),
        )

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        _ctx(request, info="Sustitución creada correctamente."),
    )
