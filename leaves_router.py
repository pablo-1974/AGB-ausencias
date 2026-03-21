from __future__ import annotations

# FastAPI — routing, dependencias y formularios
from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse

# SQLAlchemy async
from sqlalchemy import select, and_, or_, exists, not_
from sqlalchemy.ext.asyncio import AsyncSession

# Tipos y fechas
from datetime import date
from typing import Optional

# Capas internas de la aplicación
from database import get_session
from config import settings
from auth import admin_required
from models import Teacher, TeacherStatus, Leave, User

# Servicios asociados a la gestión de bajas
from services.leaves import close_leave, open_leave, set_substitution

# Servicio para replicar horarios en sustituciones
from services.schedule import clone_teacher_schedule

# Usuario autenticado vía session/cookie
from app import load_user_dep

# Normalización de nombres para ordenar sin tildes
from utils import normalize_name

# Crear router principal de bajas
router = APIRouter()


# -------------------------------------------------------------------
# Helpers de plantillas
#   - _templates: accede al motor Jinja2 almacenado en app.state
#   - _ctx: contexto común que SIEMPRE incluye "user"
# -------------------------------------------------------------------

def _templates(request: Request):
    """Devuelve el motor de plantillas configurado en la app."""
    return request.app.state.templates


def _ctx(request: Request, user: User, **extra):
    """
    Contexto base para TODAS las plantillas:
      - request
      - user (obligatorio porque base.html lo requiere)
      - título por defecto: 'Bajas'
      - info de la institución
    """
    base = {
        "request": request,
        "user": user,
        "title": "Bajas",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    # Añadir cualquier argumento extra que pase la ruta
    base.update(extra or {})
    return base


# ===================================================================
# 1) FINALIZAR BAJA
# ===================================================================
# Estas rutas permiten cerrar una baja que está activa (end_date = None)
# Pantallas:
#   - GET  /leaves/close   → lista bajas abiertas para elegir cuál cerrar
#   - POST /leaves/finish  → establece fecha fin real
# ===================================================================

@router.get("/leaves/close")
async def leaves_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Muestra una tabla con todas las bajas actualmente abiertas
    (end_date IS NULL), permitiendo seleccionar una para cerrarla.
    """
    user = admin

    # Obtener bajas sin end_date
    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(Leave.end_date.is_(None))
        )
    ).all()

    # Ordenada alfabéticamente por nombre del profesor (normalizado)
    rows = sorted(rows, key=lambda lt: normalize_name(lt[1].name))

    open_items = [
        {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
        for (l, t) in rows
    ]

    return _templates(request).TemplateResponse(
        "leaves_close.html",
        _ctx(request, user=user, title="Finalizar baja", open_items=open_items),
    )


@router.post("/leaves/finish")
async def leaves_finish(
    request: Request,
    teacher_id: int = Form(...),
    end_date: date = Form(...),
    next_url: str | None = Form(None, alias="next"),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Finaliza la baja del profesor indicado, llamando al servicio
    close_leave(). Si ocurre cualquier error, recarga la misma
    página mostrando mensaje en plantilla.
    """
    user = admin

    try:
        await close_leave(session, teacher_id=teacher_id, end_date=end_date)
        return RedirectResponse(next_url or "/leaves", status_code=303)

    except Exception as e:
        # Si hay error, recargamos la lista de bajas abiertas
        rows = (
            await session.execute(
                select(Leave, Teacher)
                .join(Teacher, Teacher.id == Leave.teacher_id)
                .where(Leave.end_date.is_(None))
            )
        ).all()

        rows = sorted(rows, key=lambda lt: normalize_name(lt[1].name))

        open_items = [
            {"teacher_id": t.id, "teacher_name": t.name, "start_date": l.start_date}
            for (l, t) in rows
        ]

        return _templates(request).TemplateResponse(
            "leaves_close.html",
            _ctx(request, user=user, open_items=open_items, error=str(e)),
            status_code=400,
        )


# ===================================================================
# 2) INICIAR BAJA
# ===================================================================
# Estas rutas permiten abrir una nueva baja:
#   - GET  /leaves/new  → formulario de creación
#   - POST /leaves/new → guarda la baja y cambia estado del profesor
# ===================================================================

@router.get("/leaves/new")
async def leaves_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Muestra un formulario con todos los profesores activos
    para iniciar una nueva baja.
    """
    user = admin

    # Listar profesores con status activo
    rows = (
        await session.execute(
            select(Teacher).where(Teacher.status == TeacherStatus.activo)
        )
    ).scalars().all()

    teachers = sorted(rows, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "leaves_new.html",
        _ctx(request, user=user, title="Iniciar baja", teachers=teachers),
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
    category: str = Form(...),
):
    """
    Guarda una nueva baja para un profesor:
      - Valida categoría (A-L)
      - Llama al servicio open_leave()
      - Cambia estado del profesor a baja o excedencia
    """
    user = admin

    # Determinar tipo de baja
    lt = TeacherStatus.baja if leave_type == "baja" else TeacherStatus.excedencia

    # Validación de categoría
    if category not in list("ABCDEFGHIJKL"):
        rows = (
            await session.execute(
                select(Teacher).where(Teacher.status == TeacherStatus.activo)
            )
        ).scalars().all()

        teachers = sorted(rows, key=lambda t: normalize_name(t.name))

        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(
                request,
                user=user,
                title="Iniciar baja",
                teachers=teachers,
                error="Debe seleccionar una categoría válida (A–L).",
            ),
            status_code=400,
        )

    try:
        await open_leave(
            session=session,
            teacher_id=teacher_id,
            start_date=start_date,
            leave_type=lt,
            cause=cause or "Baja",
            category=category,
        )

        return RedirectResponse("/leaves", status_code=303)

    except Exception as e:
        # Error creando la baja → volver al formulario
        rows = (
            await session.execute(
                select(Teacher).where(Teacher.status == TeacherStatus.activo)
            )
        ).scalars().all()

        teachers = sorted(rows, key=lambda t: normalize_name(t.name))

        return _templates(request).TemplateResponse(
            "leaves_new.html",
            _ctx(
                request,
                user=user,
                title="Iniciar baja",
                teachers=teachers,
                error=str(e),
            ),
            status_code=400,
        )


# ===================================================================
# 3) CREAR SUSTITUCIÓN
# ===================================================================
# Gestiona qué profesor sustituye a otro durante una baja:
#   - GET  /substitutions/new → formulario
#   - POST /substitutions/new → crea sustitución
#
# Incluye opciones:
#   - Reutilizar un exprofesor (estado exprofe)
#   - Crear un profesor nuevo
# ===================================================================

@router.get("/substitutions/new")
async def substitutions_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Muestra formulario para elegir:
      - Una baja abierta SIN sustituto
      - Un profesor sustituto (exprofe o nuevo)
    """
    user = admin

    # ---- 1. Bajas abiertas sin sustituto asignado ----
    rows = (
        await session.execute(
            select(Leave, Teacher)
            .join(Teacher, Teacher.id == Leave.teacher_id)
            .where(
                and_(
                    Leave.end_date.is_(None),
                    Leave.substitute_teacher_id.is_(None),
                    or_(
                        Teacher.status == TeacherStatus.baja,
                        Teacher.status == TeacherStatus.excedencia,
                    ),
                )
            )
        )
    ).all()

    open_leaves = sorted(
        [
            {
                "teacher_id": t.id,
                "teacher_name": t.name,
                "start_date": l.start_date,
            }
            for (l, t) in rows
        ],
        key=lambda x: normalize_name(x["teacher_name"]),
    )

    # ---- 2. Exprofesores disponibles ----
    subq_open_leave = (
        select(Leave.id)
        .where(and_(Leave.teacher_id == Teacher.id, Leave.end_date.is_(None)))
        .limit(1)
        .scalar_subquery()
    )

    exprofes_raw = (
        await session.execute(
            select(Teacher).where(
                and_(
                    Teacher.status == TeacherStatus.exprofe,
                    subq_open_leave.is_(None),
                )
            )
        )
    ).scalars().all()

    exprofes = sorted(exprofes_raw, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "substitutions_new.html",
        _ctx(
            request,
            user=user,
            title="Iniciar sustitución",
            open_leaves=open_leaves,
            exprofes=exprofes,
        ),
    )

# --- POST creación sustitución ---
@router.post("/substitutions/new")
async def substitutions_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    # Datos del formulario
    teacher_id: int = Form(...),
    start_date: date = Form(...),
    sub_mode: str = Form(...),
    exprof_teacher_id: Optional[str] = Form(None),
    new_name: Optional[str] = Form(None),
    new_email: Optional[str] = Form(None),
    new_alias: Optional[str] = Form(None),
):
    """
    Crea una sustitución:
      - 'exprof': reactivar exprofesor
      - 'new': crear profesor nuevo
    Luego replica el horario del profesor original.
    """
    user = admin

    # Validación del modo de sustitución
    if sub_mode not in ("exprof", "new"):
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(
                request,
                user=user,
                error="Debes elegir 'Exprofes' o 'Profesor nuevo'.",
            ),
            status_code=400,
        )

    # Verificar que el profesor tiene baja sin sustituto
    leave_row = (
        await session.execute(
            select(Leave).where(
                and_(
                    Leave.teacher_id == teacher_id,
                    Leave.end_date.is_(None),
                    Leave.substitute_teacher_id.is_(None),
                )
            )
        )
    ).scalar_one_or_none()

    if not leave_row:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(
                request,
                user=user,
                error="El profesor no tiene baja abierta sin sustituto.",
            ),
            status_code=400,
        )

    substitute_teacher_id = None

    # ------------------------------------------------------
    # Modo 1: sustituir con EXPROFESOR ya existente
    # ------------------------------------------------------
    if sub_mode == "exprof":
        exprof_teacher_id = (exprof_teacher_id or "").strip()

        if not exprof_teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, user=user, error="Debes seleccionar un exprofesor."),
                status_code=400,
            )

        # Convertir ID
        try:
            exprof_id = int(exprof_teacher_id)
        except ValueError:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(
                    request,
                    user=user,
                    error="Identificador de exprofesor no válido.",
                ),
                status_code=400,
            )

        # Obtener exprofe
        sub_t = await session.get(Teacher, exprof_id)
        if not sub_t:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, user=user, error="Exprofesor no encontrado."),
                status_code=404,
            )

        # Prevenir auto-sustitución
        if sub_t.id == teacher_id:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(
                    request,
                    user=user,
                    error="El sustituto no puede ser el mismo profesor.",
                ),
                status_code=400,
            )

        # Reactivar exprofesor
        sub_t.status = TeacherStatus.activo
        sub_t.titular = False
        substitute_teacher_id = sub_t.id

    # ------------------------------------------------------
    # Modo 2: crear PROFESOR NUEVO
    # ------------------------------------------------------
    else:
        new_name = (new_name or "").strip()
        new_email = (new_email or "").strip()
        new_alias = (new_alias or "").strip()

        # Validar campos obligatorios
        if not new_name or not new_email or not new_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(
                    request,
                    user=user,
                    error="Nombre, Email y Alias son obligatorios.",
                ),
                status_code=400,
            )

        # Verificar alias único
        exists_alias = (
            await session.execute(
                select(Teacher.id).where(Teacher.alias == new_alias)
            )
        ).scalar_one_or_none()

        if exists_alias:
            return _templates(request).TemplateResponse(
                "substitutions_new.html",
                _ctx(request, user=user, error="El alias ya existe."),
                status_code=400,
            )

        # Crear profesor nuevo
        new_t = Teacher(
            name=new_name,
            email=new_email,
            alias=new_alias,
            status=TeacherStatus.activo,
            titular=False,
        )
        session.add(new_t)
        await session.flush()  # Obtener new_t.id
        substitute_teacher_id = new_t.id

    # Guardar sustitución
    await set_substitution(
        session=session,
        teacher_id=teacher_id,
        start_date=start_date,
        substitute_teacher_id=substitute_teacher_id,
    )

    # Intentar replicar horario original
    try:
        await clone_teacher_schedule(
            session,
            source_teacher_id=teacher_id,
            target_teacher_id=substitute_teacher_id,
            effective_from=start_date,
        )
    except Exception as e:
        return _templates(request).TemplateResponse(
            "substitutions_new.html",
            _ctx(
                request,
                user=user,
                info="Sustitución creada, pero hubo un problema heredando el horario: "
                + str(e),
            ),
        )

    return RedirectResponse("/leaves", status_code=303)


# ===================================================================
# 4) VER BAJAS (listado general)
# ===================================================================
# Rutas:
#   - GET /leaves → permite filtrar bajas por:
#       - abiertas / todas
#       - con sustituto / sin sustituto
#       - orden por fecha asc/desc
# Mostrar información de:
#   - profesor titular
#   - sustituto (si lo hay)
# ===================================================================

@router.get("/leaves", response_class=HTMLResponse)
async def leaves_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    # Parámetros opcionales de filtrado
    status: str = Query("open", pattern="^(open|all)$"),
    with_sub: Optional[str] = Query(None),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    """
    Vista general de bajas:
      - Permite filtrar y ordenar
      - Muestra sustituciones si existen
    """
    user = admin

    from sqlalchemy.orm import aliased
    Sub = aliased(Teacher)

    # Query principal
    q = (
        select(Leave, Teacher, Sub)
        .join(Teacher, Teacher.id == Leave.teacher_id)
        .outerjoin(Sub, Sub.id == Leave.substitute_teacher_id)
    )

    # Filtro por bajas abiertas
    if status == "open":
        q = q.where(Leave.end_date.is_(None))

    # Filtro por existencia de sustituto
    ws = (with_sub or "").strip().lower()
    if ws == "true":
        q = q.where(Leave.substitute_teacher_id.is_not(None))
    elif ws == "false":
        q = q.where(Leave.substitute_teacher_id.is_(None))

    # Ordenación
    if order == "desc":
        q = q.order_by(Leave.start_date.desc(), Teacher.name.asc())
    else:
        q = q.order_by(Leave.start_date.asc(), Teacher.name.asc())

    rows = (await session.execute(q)).all()

    # Adaptación a plantilla
    items = []
    for lv, t, sub in rows:
        items.append(
            {
                "leave_id": lv.id,
                "teacher_id": t.id,
                "teacher_name": t.name,
                "start_date": lv.start_date,
                "cause": lv.cause or "",
                "sub_start_date": getattr(lv, "substitute_start_date", None),
                "sub_end_date": getattr(lv, "substitute_end_date", None),
                "sub_name": sub.name if sub else None,
            }
        )

    return _templates(request).TemplateResponse(
        "leaves_list.html",
        _ctx(
            request,
            user=user,
            title="Bajas (ver)",
            items=items,
            current_filters={
                "status": status,
                "with_sub": with_sub,
                "order": order,
            },
        ),
    )


# ===================================================================
# 5) ADMINISTRACIÓN DE BAJAS (NUEVO)
# ===================================================================
# Panel similar al de ausencias_admin:
#   - GET  /leaves/admin            → lista editable
#   - GET  /leaves/edit/{leave_id}  → formulario
#   - POST /leaves/edit/{leave_id}  → guardar cambios
#   - POST /leaves/delete/{leave_id}→ borrar baja
# ===================================================================

@router.get("/leaves/admin", response_class=HTMLResponse)
async def leaves_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Lista TODAS las bajas y permite:
      - Editarlas
      - Eliminarlas
    """
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
        _ctx(request, user=user, title="Edición de bajas", items=items),
    )


@router.get("/leaves/edit/{leave_id}", response_class=HTMLResponse)
async def leaves_edit_form(
    leave_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Abre formulario para editar una baja existente.
    Muestra:
      - Profesor (readonly)
      - Fechas inicio/fin
      - Motivo
    """
    user = admin

    
    l = await session.get(Leave, leave_id)
    if not l:
        return RedirectResponse("/leaves/admin", 303)

    t = await session.get(Teacher, l.teacher_id)

    return _templates(request).TemplateResponse(
        "leaves_edit.html",
        _ctx(
            request,
            user=user,
            title="Editar baja",
            leave=l,
            teacher=t,
            categories=list("ABCDEFGHIJKL"),     # ← NECESARIO
            current_category=(l.category or ""), # ← NECESARIO
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
    """
    Guarda cambios en una baja:
      - fecha inicio
      - fecha fin
      - motivo
    """
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
    """
    Borra completamente una baja.
    (DELETE real, irreversible)
    """
    l = await session.get(Leave, leave_id)
    if l:
        await session.delete(l)
        await session.commit()

    return RedirectResponse("/leaves/admin", 303)
