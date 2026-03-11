# absences_router.py
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, List, Tuple

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

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
# Utilidades de horas (mask 6 bits)
#  bit0..bit5 -> 1ª..6ª
# -----------------------------
HOUR_LABELS = ["1ª", "2ª", "3ª", "4ª", "5ª", "6ª"]

def make_mask_all() -> int:
    return (1 << 6) - 1  # 0b111111 -> 63

def make_mask_range(from_idx: int, to_idx: int) -> int:
    """from_idx / to_idx: 0..5 inclusivo"""
    if from_idx > to_idx:
        from_idx, to_idx = to_idx, from_idx
    mask = 0
    for i in range(from_idx, to_idx + 1):
        mask |= (1 << i)
    return mask

def mask_to_human(mask: int) -> str:
    """Convierte mask a texto humano: 'Todas' o '1ª-3ª' o '1ª, 3ª'."""
    if mask <= 0:
        return "—"
    if mask == make_mask_all():
        return "Todas"
    on = [i for i in range(6) if (mask >> i) & 1]
    # Comprimir a rangos
    ranges: List[Tuple[int, int]] = []
    start = on[0]
    prev = on[0]
    for i in on[1:]:
        if i == prev + 1:
            prev = i
        else:
            ranges.append((start, prev))
            start, prev = i, i
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
    # Por defecto: hoy–hoy
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

    # Normalizar orden (from <= to)
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    # Consultar ausencias entre fechas (incluido)
    res = await session.execute(
        select(Absence, Teacher)
        .join(Teacher, Teacher.id == Absence.teacher_id)
        .where(
            and_(
                Absence.date >= d_from,
                Absence.date <= d_to,
            )
        )
        .order_by(Absence.date.asc(), Teacher.name.asc())
    )
    rows = res.all()  # (Absence, Teacher)

    items = [{
        "day": a.date,
        "teacher_name": t.name,
        "hours_str": mask_to_human(a.hours_mask or 0),
        "cause": (a.note or "").strip(),
        "teacher_id": t.id,
    } for (a, t) in rows]

    # Mensaje si no hay resultados
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
            filters={
                "from": d_from.isoformat(),
                "to": d_to.isoformat(),
            },
        ),
    )


# ==================================
# NUEVA AUSENCIA (GET/POST)
# ==================================
def _teachers_active_on(session: AsyncSession, target: date):
    """
    Devuelve un select para profes con status=activo y SIN leave que cubra 'target'.
    """
    # Un leave que cubra esa fecha: start <= target <= end (o end NULL)
    leave_cover = and_(
        Leave.teacher_id == Teacher.id,
        Leave.start_date <= target,
        or_(Leave.end_date.is_(None), Leave.end_date >= target),
    )
    # Profes activos y sin leave ese día
    return (
        select(Teacher)
        .where(
            and_(
                Teacher.status == TeacherStatus.activo,
                ~select(Leave.id).where(leave_cover).exists()
            )
        )
        .order_by(Teacher.name.asc())
    )

@router.get("/absences/new", response_class=HTMLResponse)
async def absences_new_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    d: Optional[str] = Query(None),  # fecha seleccionada en el form (para recalcular activos)
):
    target = date.fromisoformat(d) if d else date.today()

    teachers = (await session.execute(_teachers_active_on(session, target))).scalars().all()

    return _templates(request).TemplateResponse(
        "absences_new.html",
        _ctx(
            request,
            title="Nueva ausencia",
            target=target,
            teachers=teachers,
        ),
    )

@router.post("/absences/new")
async def absences_new_create(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin=Depends(admin_required),
    # Campos del formulario
    day: date = Form(...),
    teacher_id: int = Form(...),
    hours_mode: str = Form(...),     # 'all' | 'range'
    hour_from: Optional[int] = Form(None),
    hour_to: Optional[int] = Form(None),
    cause: str = Form(...),
):
    # Validaciones mínimas
    cause = (cause or "").strip()
    if not cause:
        # Volver al form con error
        teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
        return _templates(request).TemplateResponse(
            "absences_new.html",
            _ctx(request, title="Nueva ausencia", target=day, teachers=teachers, error="La causa es obligatoria."),
            status_code=400,
        )

    # Construir mask de horas
    if hours_mode == "all":
        mask = make_mask_all()
    else:
        # Deben venir hour_from y hour_to (0..5)
        try:
            fi = int(hour_from) if hour_from is not None else None
            ti = int(hour_to) if hour_to is not None else None
        except Exception:
            fi, ti = None, None

        if fi is None or ti is None or not (0 <= fi <= 5) or not (0 <= ti <= 5):
            teachers = (await session.execute(_teachers_active_on(session, day))).scalars().all()
            return _templates(request).TemplateResponse(
                "absences_new.html",
                _ctx(request, title="Nueva ausencia", target=day, teachers=teachers,
                     error="Selecciona un rango válido de horas."),
                status_code=400,
            )
        mask = make_mask_range(fi, ti)

    # Insertar o actualizar ausencia (por unique uq_teacher_date)
    existing = (await session.execute(
        select(Absence).where(and_(Absence.teacher_id == teacher_id, Absence.date == day))
    )).scalar_one_or_none()

    if existing:
        existing.hours_mask = mask
        existing.note = cause
    else:
        ins = Absence(
            teacher_id=teacher_id,
            date=day,
            hours_mask=mask,
            note=cause,
        )
        session.add(ins)

    await session.commit()

    # Volver a Ver Ausencias filtrando por ese mismo día
    return RedirectResponse(f"/absences/manage?from={day.isoformat()}&to={day.isoformat()}", status_code=303)
