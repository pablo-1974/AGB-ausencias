# absences_router.py
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
from models import Teacher, TeacherStatus, Leave, Absence

router = APIRouter()

# -----------------------------
# Helpers de plantillas/contexto
# -----------------------------
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
        "title": "Ausencias",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# -----------------------------
# Sistema de 7 franjas (0..6)
# 0=1ª, 1=2ª, 2=3ª, 3=Recreo, 4=4ª, 5=5ª, 6=6ª
# -----------------------------
HOUR_LABELS = ["1ª", "2ª", "3ª", "Recreo", "4ª", "5ª", "6ª"]

def make_mask_all():
    """Mask con TODAS las 7 franjas ON."""
    return (1 << 7) - 1   # 0b1111111 = 127

def make_mask_range(from_idx: int, to_idx: int) -> int:
    """Construye una máscara desde/hasta para franjas 0..6."""

    if from_idx > to_idx:
        from_idx, to_idx = to_idx, from_idx

    mask = 0
    for i in range(from_idx, to_idx + 1):
        mask |= (1 << i)
    return mask

def mask_to_human(mask: int) -> str:
    """Convierte mask en texto humano, usando las 7 franjas."""

    if mask <= 0:
        return "—"

    if mask == make_mask_all():
        return "Todas"

    # AHORA: 7 franjas reales (0..6)
    on = [i for i in range(7) if (mask >> i) & 1]

    if not on:
        return "—"

    # Comprimir a rangos consecutivos
    ranges: List[Tuple[int, int]] = []
    start = on[0]
    prev = on[0]

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


# ==================================
# VER AUSENCIAS (con filtros)
# ==================================
@router.get("/absences/manage", response_class=HTMLResponse)
async def absences_manage(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
    today = date.today()
    try:
        d_from = date.fromisoformat(since) if since else today
    except Exception:
        d_from = today
    try:
        d_to = date.fromisoformat(until) if until else today
    except Exception:
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

    info = None
    if not items:
        info = "No hay ausencias para el rango seleccionado."

    return _templates(request).TemplateResponse(
        "absences_manage.html",
        _ctx(
            request,
            title="Ver Ausencias",
            items=items,
            info=info,
            filters={"from": d_from.isoformat(), "to": d_to.isoformat()},
        ),
    )


# =====================================
# CATALOGAR AUSENCIAS
# =====================================
ABSENCE_CATEGORIES = [
    ("A", "Enfermedad de duración superior a tres días"),
    ("B", "Matrimonio"),
    ("C", "Embarazo"),
    ("D", "Licencia por estudios"),
    ("E", "Asuntos propios"),
    ("F", "Actividades de perfeccionamiento"),
    ("G", "Nacimiento de un hijo, enfermedad de un familiar"),
    ("H", "Traslado de domicilio, funciones sindicales, c. exámenes …"),
    ("I", "Deber inexcusable de carácter público o personal"),
    ("J", "Asistencia a consulta médica"),
    ("K", "Enfermedad de 1 a tres días"),
    ("L", "\"Moscosos\" y otros motivos"),
    ("Z", "Actividad del CENTRO"),
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
    admin=Depends(admin_required),
    scope: str = Query("pending", pattern="^(pending|all)$"),
    since: Optional[str] = Query(None, alias="from"),
    until: Optional[str] = Query(None, alias="to"),
):
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

    info = None
    if not items:
        info = "No hay ausencias para los filtros seleccionados."

    return _templates(request).TemplateResponse(
        "absences_categorize.html",
        _ctx(
            request,
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
    admin=Depends(admin_required),
    absence_id: int = Form(...),
    category: str = Form(...),
    next_url: Optional[str] = Form(None, alias="next"),
):
    valid_codes = {code for code, _ in ABSENCE_CATEGORIES}
    category = (category or "").strip().upper()

    if category not in valid_codes:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", status_code=303)

    a = await session.get(Absence, absence_id)
    if not a:
        return RedirectResponse(next_url or "/absences/categorize?scope=pending", status_code=303)

    a.category = category
    await session.commit()

    return RedirectResponse(next_url or "/absences/categorize?scope=pending", status_code=303)


# ==================================
# NUEVA AUSENCIA (GET/POST)
# ==================================
def _teachers_active_on(session: AsyncSession, target: date):
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
    admin=Depends(admin_required),
    d: Optional[str] = Query(None),
):
    target = date.fromisoformat(d) if d else date.today()
    teachers = (await session.execute(_teachers_active_on(session, target))).scalars().all()

    return _templates(request).TemplateResponse(
        "absences_new.html",
        _ctx(request, title="Nueva ausencia", target=target, teachers=teachers),
    )


@router.post("/absences/new")
async def absences_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    day: date = Form(...),
    teacher_id: int = Form(...),
    hours_mode: str = Form(...),
    hour_from: Optional[int] = Form(None),
    hour_to: Optional[int] = Form(None),
    cause: str = Form(...),
):
    cause = (cause or "").strip()
    if not cause:
        teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
        return _templates(request).TemplateResponse(
            "absences_new.html",
            _ctx(request, title="Nueva ausencia", target=day, teachers=teachers,
                 error="La causa es obligatoria."),
            status_code=400,
        )

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
                _ctx(request, title="Nueva ausencia", target=day, teachers=teachers,
                     error="Selecciona un rango válido de horas."),
                status_code=400,
            )

        mask = make_mask_range(fi, ti)

    existing = (await session.execute(
        select(Absence).where(and_(Absence.teacher_id == teacher_id, Absence.date == day))
    )).scalar_one_or_none()

    if existing:
        existing.hours_mask = mask
        existing.note = cause
    else:
        session.add(Absence(
            teacher_id=teacher_id,
            date=day,
            hours_mask=mask,
            note=cause,
        ))

    await session.commit()

    return RedirectResponse(f"/absences/manage?from={day.isoformat()}&to={day.isoformat()}", status_code=303)
