# ======================================================
# absences_router.py — Rutas de gestión de AUSENCIAS
# ======================================================
# Contiene:
#   - Visualización por rango
#   - Catalogación de ausencias
#   - Creación de nuevas ausencias
#   - Edición y borrado
# Rutas de gestión diaria accesibles a Jefatura (user).
# Edición y borrado reservados exclusivamente a admin.
# ======================================================

from __future__ import annotations

from datetime import date
from typing import Optional, List, Tuple

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, and_, or_, exists, not_
from sqlalchemy.ext.asyncio import AsyncSession
import calendar

from database import get_session
from config import settings
from auth import admin_required
from models import Teacher, TeacherStatus, Leave, Absence, User

from services.actions_log import log_action
from utils import ActionType

# Usuario autenticado
from app import load_user_dep

# Helpers externos
from utils import normalize_name

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter()


# ======================================================
# Helpers Jinja2
# ======================================================
def _templates(request: Request):
    """Accede al motor de plantillas configurado en app.state."""
    return request.app.state.templates


# ======================================================
# SISTEMA DE 7 FRANJAS (máscaras de horas)
# ======================================================

HOUR_LABELS = ["1ª", "2ª", "3ª", "Recreo", "4ª", "5ª", "6ª"]


def make_mask_all():
    """Máscara que representa TODAS las horas (0–6)."""
    return (1 << 7) - 1


def make_mask_range(from_idx: int, to_idx: int) -> int:
    """Convierte un rango de horas (p.ej. 1 a 3) en una máscara binaria."""
    if from_idx > to_idx:
        from_idx, to_idx = to_idx, from_idx
    mask = 0
    for i in range(from_idx, to_idx + 1):
        mask |= (1 << i)
    return mask


def mask_to_human(mask: int) -> str:
    """Convierte una máscara de horas en formato legible (ej. '1ª-3ª')."""
    if mask <= 0:
        return "—"
    if mask == make_mask_all():
        return "Todas"

    on = [i for i in range(7) if (mask >> i) & 1]
    if not on:
        return "—"

    ranges: List[Tuple[int, int]] = []
    start = prev = on[0]

    for i in on[1:]:
        if i == prev + 1:
            prev = i
        else:
            ranges.append((start, prev))
            start = prev = i
    ranges.append((start, prev))

    parts = []
    for a, b in ranges:
        if a == b:
            parts.append(HOUR_LABELS[a])
        else:
            parts.append(f"{HOUR_LABELS[a]}-{HOUR_LABELS[b]}")
    return ", ".join(parts)


# ======================================================
# /absences/manage — Ver ausencias en rango
# ======================================================

@router.get("/absences/manage", response_class=HTMLResponse)
async def absences_manage(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
    """Vista principal para ver ausencias entre fechas."""
  
    today = date.today()
    try:
        d_from = date.fromisoformat(since) if since else today
    except:
        d_from = today

    try:
        d_to = date.fromisoformat(until) if until else today
    except:
        d_to = today

    if d_from > d_to:
        d_from, d_to = d_to, d_from

    res = await session.execute(
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(and_(Absence.date >= d_from, Absence.date <= d_to))
        .order_by(Absence.date.asc(), Teacher.name.asc())
    )
    rows = res.all()

    items = [{
        "day": a.date,
        "teacher_name": t.name,
        "hours_str": mask_to_human(a.hours_mask or 0),
        "cause": (a.note or "").strip(),
        "teacher_id": t.id,
    } for (a, t) in rows]

    info = None if items else "No hay ausencias para el rango seleccionado."

    return _templates(request).TemplateResponse(
        "absences_manage.html",
        ctx(
            request,
            user,
            title="Ver Ausencias",
            items=items,
            info=info,
            filters={"from": d_from.isoformat(), "to": d_to.isoformat()},
        ),
    )


# ======================================================
# Catalogación de ausencias
# ======================================================

ABSENCE_CATEGORIES = [
    ("A", "Enfermedad >3 días"),
    ("B", "Matrimonio"),
    ("C", "Embarazo"),
    ("D", "Licencia por estudios"),
    ("E", "Asuntos propios"),
    ("F", "Perfeccionamiento"),
    ("G", "Nacimiento hijo / familiar enfermo"),
    ("H", "Traslado / sindicatos / exámenes"),
    ("I", "Deber inexcusable"),
    ("J", "Consulta médica"),
    ("K", "Enfermedad 1–3 días"),
    ("L", "Moscosos y otros"),
    ("Z", "Actividad del centro"),
]


def _first_day_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def _last_day_of_month(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last)


@router.get("/absences/categorize", response_class=HTMLResponse)
async def absences_categorize(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
    scope: str = Query("pending", pattern="^(pending|all)$"),
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
    """Pantalla para clasificar ausencias por categoría."""
  
    today = date.today()
    d_from = date.fromisoformat(since) if since else _first_day_of_month(today)
    d_to = date.fromisoformat(until) if until else _last_day_of_month(today)

    if d_from > d_to:
        d_from, d_to = d_to, d_from

    q = (
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(and_(Absence.date >= d_from, Absence.date <= d_to))
    )
    if scope == "pending":
        q = q.where(or_(Absence.category.is_(None), Absence.category == ""))

    rows = (await session.execute(q.order_by(Absence.date.asc(), Teacher.name.asc()))).all()

    items = [{
        "id": a.id,
        "day": a.date,
        "teacher_name": t.name,
        "hours_str": mask_to_human(a.hours_mask or 0),
        "cause": (a.note or "").strip(),
        "category": (a.category or "").strip(),
    } for (a, t) in rows]

    categories_map = {code: label for code, label in ABSENCE_CATEGORIES}
    info = None if items else "No hay ausencias para los filtros seleccionados."

    return _templates(request).TemplateResponse(
        "absences_categorize.html",
        ctx(
            request,
            user,
            title="Catalogar ausencias",
            items=items,
            categories=ABSENCE_CATEGORIES,
            categories_map=categories_map,
            filters={"scope": scope, "from": d_from.isoformat(), "to": d_to.isoformat()},
            info=info,
        ),
    )


@router.post("/absences/categorize")
async def absences_categorize_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
    absence_id: int = Form(...),
    category: str = Form(...),
    next_url: Optional[str] = Form(None, alias="next"),
):
    """Guarda la clasificación asignada a una ausencia."""
    valid_codes = {code for code, _ in ABSENCE_CATEGORIES}
    category = (category or "").strip().upper()

    if category not in valid_codes:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)

    # ✅ Guardar categoría anterior (para el log)
    old_category = a.category

    # Guardar nueva categoría
    a.category = category
    await session.commit()

    # ✅ REGISTRO DE ACCIÓN: CATALOGACIÓN DE AUSENCIA
    await log_action(
        session,
        user=user,
        action=ActionType.ABSENCE_CATEGORIZE,
        entity="absence",
        entity_id=a.id,
        detail=(
            f"Ausencia catalogada como {category}"
            if not old_category
            else f"Categoría cambiada de {old_category} a {category}"
        ),
    )

    await session.commit()

    return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)


# ======================================================
# NUEVA AUSENCIA
# ======================================================

def _teachers_active_on(session: AsyncSession, target: date):
    """
    Devuelve profesores activos en esa fecha.
    Se excluyen solo:
      - profesores cuyo estado NO sea 'activo'
      - profesores con una baja raíz (parent_leave_id IS NULL) vigente ese día
    Las bajas hijas (sustitutos) NO se consideran baja propia.
    """

    # Baja raíz vigente (solo baja propia, no sustituto)
    own_leave = exists().where(
        and_(
            Leave.teacher_id == Teacher.id,
            Leave.parent_leave_id.is_(None),               # ✅ solo bajas raíz
            Leave.start_date <= target,
            or_(Leave.end_date.is_(None), Leave.end_date >= target),
        )
    )

    return (
        select(Teacher)
        .where(
            and_(
                Teacher.status == TeacherStatus.activo,
                not_(own_leave)
            )
        )
        .order_by(Teacher.name.asc())
    )


@router.get("/absences/new", response_class=HTMLResponse)
async def absences_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
    d: Optional[str] = Query(None),
):
    """Formulario de nueva ausencia."""

    target = date.fromisoformat(d) if d else date.today()
    teachers = (await session.execute(_teachers_active_on(session, target))).scalars().all()

    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "absences_new.html",
        ctx(
            request,
            user,
            title="Nueva ausencia",
            target=target,
            teachers=teachers,
        ),
    )


@router.post("/absences/new")
async def absences_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep),
    day: date = Form(...),
    teacher_id: int = Form(...),
    hours_mode: str = Form(...),
    hour_from: Optional[int] = Form(None),
    hour_to: Optional[int] = Form(None),
    cause: str = Form(...),
):
    """Procesa la creación de una ausencia."""

    cause = (cause or "").strip()

    if not cause:
        teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
        teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

        return _templates(request).TemplateResponse(
            "absences_new.html",
            ctx(
                request,
                user,
                title="Nueva ausencia",
                target=day,
                teachers=teachers,
                error="La causa es obligatoria.",
            ),
            status_code=400,
        )

    if hours_mode == "all":
        mask = make_mask_all()
    else:
        try:
            fi = int(hour_from)
            ti = int(hour_to)
        except:
            fi = ti = None

        if fi is None or ti is None or not (0 <= fi <= 6) or not (0 <= ti <= 6):
            teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
            return _templates(request).TemplateResponse(
                "absences_new.html",
                ctx(
                    request,
                    user,
                    title="Nueva ausencia",
                    target=day,
                    teachers=teachers,
                    error="Selecciona un rango válido de horas.",
                ),
                status_code=400,
            )

        mask = make_mask_range(fi, ti)

    # Insertar o actualizar ausencia
    existing = (
        await session.execute(
            select(Absence).where(and_(Absence.teacher_id == teacher_id, Absence.date == day))
        )
    ).scalar_one_or_none()

    if existing:
        existing.hours_mask = mask
        existing.note = cause
    else:
        session.add(
            Absence(
                teacher_id=teacher_id,
                date=day,
                hours_mask=mask,
                note=cause,
            )
        )

    await session.commit()
    
    # ✅ REGISTRO DE ACCIÓN: CREAR / ACTUALIZAR AUSENCIA
    action = ActionType.ABSENCE_UPDATE if existing else ActionType.ABSENCE_CREATE
    
    absence = existing
    if not absence:
        absence = (
            await session.execute(
                select(Absence)
                .where(Absence.teacher_id == teacher_id, Absence.date == day)
            )
        ).scalar_one()
    
    await log_action(
        session,
        user=user,
        action=action,
        entity="absence",
        entity_id=absence.id,
        detail=f"Ausencia {'modificada' if existing else 'creada'} para el día {day.strftime('%d/%m/%Y')}",
    )

    await session.commit()

    return RedirectResponse(
        f"/absences/manage?from={day.isoformat()}&to={day.isoformat()}",
        303,
    )


# ======================================================
# ADMIN DE AUSENCIAS
# ======================================================

@router.get("/absences/admin")
async def absences_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(load_user_dep)
):
    """Panel admin para editar o borrar ausencias."""

    rows = (
        await session.execute(
            select(Absence, Teacher)
            .join(Teacher, Teacher.id == Absence.teacher_id)
            .order_by(Absence.date.desc(), Teacher.name.asc())
        )
    ).all()

    items = [{
        "id": a.id,
        "day": a.date,
        "teacher_name": t.name,
        "hours_str": mask_to_human(a.hours_mask or 0),
        "cause": (a.note or "").strip(),
        "category": (a.category or "").strip(),
    } for (a, t) in rows]

    return _templates(request).TemplateResponse(
        "absences_admin_list.html",
        ctx(request, user, title="Edición de ausencias", items=items),
    )


@router.get("/absences/edit/{absence_id}")
async def absences_edit_form(
    absence_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Formulario de edición de una ausencia."""
    user = admin

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse("/absences/admin", 303)

    t = await session.get(Teacher, a.teacher_id)

    return _templates(request).TemplateResponse(
        "absences_edit.html",
        ctx(
            request,
            user,
            title="Editar ausencia",
            absence=a,
            teacher=t,
            hours_str=mask_to_human(a.hours_mask or 0),
            categories=ABSENCE_CATEGORIES,
            current_category=(a.category or "").strip(),
        ),
    )


@router.post("/absences/edit/{absence_id}")
async def absences_edit_save(
    absence_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    date_: date = Form(...),
    hours_mode: str = Form(...),
    hour_from: Optional[int] = Form(None),
    hour_to: Optional[int] = Form(None),
    cause: str = Form(""),
    category: str = Form(""),
):
    """Guarda cambios en una ausencia existente."""
    user = admin

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse("/absences/admin", 303)

    cause = (cause or "").strip()

    if hours_mode == "all":
        mask = make_mask_all()
    else:
        # Resolver rango SOLO si es necesario
        try:
            fi = int(hour_from) if hour_from is not None else None
            ti = int(hour_to) if hour_to is not None else None
        except:
            fi = ti = None
    
        if fi is None or ti is None or not (0 <= fi <= 6) or not (0 <= ti <= 6):
            return RedirectResponse("/absences/admin", 303)
    
        mask = make_mask_range(fi, ti)

    a.date = date_
    a.hours_mask = mask
    a.note = cause
    a.category = (category or "").strip()

    await session.commit()

    # ✅ REGISTRO DE ACCIÓN: EDICIÓN DE AUSENCIA
    await log_action(
        session,
        user=admin,
        action=ActionType.ABSENCE_UPDATE,
        entity="absence",
        entity_id=a.id,
        detail=f"Ausencia editada ({a.date.strftime('%d/%m/%Y')})",
    )

    await session.commit()
    
    return RedirectResponse("/absences/admin", 303)


@router.post("/absences/delete/{absence_id}")
async def absences_delete(
    absence_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """Borrado permanente de una ausencia."""
    a = await session.get(Absence, absence_id)
    if a:
        absence_id = a.id
        absence_date = a.date
    
        await session.delete(a)
        await session.commit()
    
        # ✅ REGISTRO DE ACCIÓN: BORRADO DE AUSENCIA
        await log_action(
            session,
            user=admin,
            action=ActionType.ABSENCE_DELETE,
            entity="absence",
            entity_id=absence_id,
            detail=f"Ausencia eliminada ({absence_date.strftime('%d/%m/%Y')})",
        )

        await session.commit()
        
    return RedirectResponse("/absences/admin", 303)
