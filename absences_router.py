# absences_router.py
# ======================================================
# absences_router.py — RUTAS DE AUSENCIAS
# Documentado: indica para qué sirve cada ruta,
# quién puede acceder (admin / usuario), a qué plantilla
# redirige y qué hace exactamente.
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
from auth import admin_required              # ← protege rutas admin
from models import Teacher, TeacherStatus, Leave, Absence, User

# 🔥 Para obtener el usuario logueado (aunque la mayoría son admin)
from app import load_user_dep

from utils import normalize_name

router = APIRouter()


# ======================================================
# Helpers de plantillas / contexto
# ======================================================
def _templates(request: Request):
    """Devuelve el motor de plantillas."""
    return request.app.state.templates


def _ctx(request: Request, user: User, **extra):
    """
    Contexto común: incluye SIEMPRE user porque base.html
    necesita user, rol, logo_path, etc.
    """
    base = {
        "request": request,
        "user": user,
        "title": "Ausencias",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# ======================================================
# SISTEMA DE 7 FRANJAS (para representar horas de ausencia)
# Helpers internos, NO son rutas.
# ======================================================
HOUR_LABELS = ["1ª", "2ª", "3ª", "Recreo", "4ª", "5ª", "6ª"]


def make_mask_all():
    """Máscara completa (todas las horas)."""
    return (1 << 7) - 1


def make_mask_range(from_idx: int, to_idx: int) -> int:
    """Máscara para un rango de horas (p. ej. 1ª-3ª)."""
    if from_idx > to_idx:
        from_idx, to_idx = to_idx, from_idx
    mask = 0
    for i in range(from_idx, to_idx + 1):
        mask |= (1 << i)
    return mask


def mask_to_human(mask: int) -> str:
    """Convierte la máscara de horas en texto legible."""
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
# RUTA: /absences/manage (GET) — ACCESO: SOLO ADMIN
# Vista principal para ver ausencias en un rango.
# Plantilla: absences_manage.html
# ======================================================
@router.get("/absences/manage", response_class=HTMLResponse)
async def absences_manage(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),   # admin_required → usuario admin
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
    user = admin   # base.html necesita "user"

    # Cálculo de rango fechas
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

    # Obtener ausencias y profesores relacionados
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

    info = "No hay ausencias para el rango seleccionado." if not items else None

    return _templates(request).TemplateResponse(
        "absences_manage.html",
        _ctx(
            request,
            user=user,
            title="Ver Ausencias",
            items=items,
            info=info,
            filters={"from": d_from.isoformat(), "to": d_to.isoformat()},
        ),
    )


# ======================================================
# RUTA: /absences/categorize (GET) — SOLO ADMIN
# Permite clasificar ausencias por categoría.
# Plantilla: absences_categorize.html
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
    admin: User = Depends(admin_required),
    scope: str = Query("pending", pattern="^(pending|all)$"),
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
    user = admin   # plantilla necesita user

    # Preparar rango
    today = date.today()
    d_from = date.fromisoformat(since) if since else _first_day_of_month(today)
    d_to = date.fromisoformat(until) if until else _last_day_of_month(today)

    if d_from > d_to:
        d_from, d_to = d_to, d_from

    # Consulta
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
        _ctx(
            request,
            user=user,
            title="Catalogar ausencias",
            items=items,
            categories=ABSENCE_CATEGORIES,
            categories_map=categories_map,
            filters={"scope": scope, "from": d_from.isoformat(), "to": d_to.isoformat()},
            info=info,
        ),
    )


# ======================================================
# RUTA: /absences/categorize (POST) — SOLO ADMIN
# Guarda la categoría seleccionada.
# ======================================================
@router.post("/absences/categorize")
async def absences_categorize_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    absence_id: int = Form(...),
    category: str = Form(...),
    next_url: Optional[str] = Form(None, alias="next"),
):
    valid_codes = {code for code, _ in ABSENCE_CATEGORIES}
    category = (category or "").strip().upper()

    # Categoría inválida
    if category not in valid_codes:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)

    a.category = category
    await session.commit()

    return RedirectResponse(next_url or "/absences/categorize?scope=pending", 303)


# ======================================================
# RUTAS: NUEVA AUSENCIA (GET/POST) — SOLO ADMIN
# Plantillas: absences_new.html
# ======================================================
def _teachers_active_on(session: AsyncSession, target: date):
    """
    Devuelve profesores activos ese día (sin baja activa).
    """
    leave_cover = and_(
        Leave.teacher_id == Teacher.id,
        Leave.start_date <= target,
        or_(Leave.end_date.is_(None), Leave.end_date >= target),
    )
    leave_exists = exists().where(leave_cover)

    return (
        select(Teacher)
        .where(and_(Teacher.status == TeacherStatus.activo, not_(leave_exists)))
        .order_by(Teacher.name.asc())
    )


@router.get("/absences/new", response_class=HTMLResponse)
async def absences_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    d: Optional[str] = Query(None),
):
    user = admin

    target = date.fromisoformat(d) if d else date.today()
    teachers = (await session.execute(_teachers_active_on(session, target))).scalars().all()
    teachers = sorted(teachers, key=lambda t: normalize_name(t.name))

    return _templates(request).TemplateResponse(
        "absences_new.html",
        _ctx(
            request,
            user=user,
            title="Nueva ausencia",
            target=target,
            teachers=teachers,
        ),
    )


@router.post("/absences/new")
async def absences_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
    day: date = Form(...),
    teacher_id: int = Form(...),
    hours_mode: str = Form(...),
    hour_from: Optional[int] = Form(None),
    hour_to: Optional[int] = Form(None),
    cause: str = Form(...),
):
    user = admin

    cause = (cause or "").strip()
    if not cause:
        teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
        teachers = sorted(teachers, key=lambda t: normalize_name(t.name))
        return _templates(request).TemplateResponse(
            "absences_new.html",
            _ctx(
                request,
                user=user,
                title="Nueva ausencia",
                target=day,
                teachers=teachers,
                error="La causa es obligatoria.",
            ),
            status_code=400,
        )

    # Convertir horas en máscara
    if hours_mode == "all":
        mask = make_mask_all()
    else:
        try:
            fi = int(hour_from) if hour_from is not None else None
            ti = int(hour_to) if hour_to is not None else None
        except ValueError:
            fi = ti = None

        if fi is None or ti is None or not (0 <= fi <= 6) or not (0 <= ti <= 6):
            teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
            return _templates(request).TemplateResponse(
                "absences_new.html",
                _ctx(
                    request,
                    user=user,
                    title="Nueva ausencia",
                    target=day,
                    teachers=teachers,
                    error="Selecciona un rango válido de horas.",
                ),
                status_code=400,
            )

        mask = make_mask_range(fi, ti)

    # Guardar o actualizar ausencia
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

    return RedirectResponse(
        f"/absences/manage?from={day.isoformat()}&to={day.isoformat()}",
        status_code=303,
    )


# ======================================================
# RUTA: /absences/admin (GET) — SOLO ADMIN
# Panel central de edición de ausencias
# ======================================================
@router.get("/absences/admin")
async def absences_admin_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = admin

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
    } for (a, t) in rows]

    return _templates(request).TemplateResponse(
        "absences_admin_list.html",
        _ctx(
            request,
            user=user,
            title="Edición de ausencias",
            items=items,
        )
    )


# ======================================================
# RUTA: /absences/edit/{id} (GET) — SOLO ADMIN
# Abre formulario para editar una ausencia
# ======================================================
@router.get("/absences/edit/{absence_id}")
async def absences_edit_form(
    absence_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    user = admin

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse("/absences/admin", 303)

    t = await session.get(Teacher, a.teacher_id)

    return _templates(request).TemplateResponse(
        "absences_edit.html",
        _ctx(
            request,
            user=user,
            title="Editar ausencia",
            absence=a,
            teacher=t,
            hours_str=mask_to_human(a.hours_mask or 0),
        ),
    )


# ======================================================
# RUTA: /absences/edit/{id} (POST) — SOLO ADMIN
# Guarda los cambios en una ausencia
# ======================================================
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
):
    user = admin

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse("/absences/admin", 303)

    cause = (cause or "").strip()

    # Máscara de horas
    if hours_mode == "all":
        mask = make_mask_all()
    else:
        try:
            fi = int(hour_from)
            ti = int(hour_to)
            mask = make_mask_range(fi, ti)
        except:
            return RedirectResponse("/absences/admin", 303)

    # Guardar cambios
    a.date = date_
    a.hours_mask = mask
    a.note = cause
    await session.commit()

    return RedirectResponse("/absences/admin", 303)


# ======================================================
# RUTA: /absences/delete/{id} (POST) — SOLO ADMIN
# Borra una ausencia (DELETE real)
# ======================================================
@router.post("/absences/delete/{absence_id}")
async def absences_delete(
    absence_id: int,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    a = await session.get(Absence, absence_id)
    if a:
        await session.delete(a)
        await session.commit()

    return RedirectResponse("/absences/admin", 303)
