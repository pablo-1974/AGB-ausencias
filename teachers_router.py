# teachers_router.py
from __future__ import annotations
from datetime import date
from typing import Iterable, Dict, List, Tuple, Optional
import unicodedata  # <-- para ordenar ignorando tildes

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from starlette.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from config import settings
from models import Teacher
from services.pdf_teachers import generate_teachers_list_pdf

# Intentar importar el modelo de sustituciones; si no existe, seguimos en modo “sin sustituciones”
try:
    from models import Substitution  # <-- si en tu proyecto tiene otro nombre, cámbialo aquí
except Exception:
    Substitution = None  # tolerante: el router funciona sin este modelo

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
        "title": "Listado de profesorado",
        "app_name": settings.APP_NAME,
        "institution_name": settings.INSTITUTION_NAME,
        "logo_path": settings.LOGO_PATH,
    }
    base.update(extra or {})
    return base


# ------- helper de ordenación sin tildes -------
def _sort_key(name: str) -> str:
    """
    Clave de ordenación que ignora tildes/diacríticos y compara en minúsculas.
    'Álvarez' -> 'alvarez'
    """
    if not name:
        return ""
    nf = unicodedata.normalize("NFD", name)
    no_diacritics = "".join(ch for ch in nf if not unicodedata.combining(ch))
    return no_diacritics.lower()


# ------- helpers sustituciones -------
def _get_attr(obj, candidates: Iterable[str], default=None):
    for c in candidates:
        if hasattr(obj, c):
            return getattr(obj, c)
    return default

def _teacher_id(value) -> Optional[int]:
    if value is None:
        return None
    try:
        if hasattr(value, "id"):
            return int(value.id)
        return int(value)
    except Exception:
        return None

def _extract_ids_and_is_active(s) -> Tuple[Optional[int], Optional[int], bool]:
    """
    ADAPTA nombres si tu modelo usa otros:
      substitute_id  -> ['substitute_id','substitute_teacher_id','sub_id','substitute']
      replaced_id    -> ['replaced_id','replaced_teacher_id','orig_id','teacher_id','replaced']
      start/end date -> ['start_date'/'end_date'] o ['since'/'until']
    """
    sub = _get_attr(s, ["substitute_id", "substitute_teacher_id", "sub_id", "substitute"])
    rep = _get_attr(s, ["replaced_id", "replaced_teacher_id", "orig_id", "teacher_id", "replaced"])
    sub_id = _teacher_id(sub)
    rep_id = _teacher_id(rep)

    start = _get_attr(s, ["start_date", "since"])
    end   = _get_attr(s, ["end_date", "until"])
    today = date.today()
    active = True
    if start and start > today:
        active = False
    if end and end < today:
        active = False
    return sub_id, rep_id, active


# ------- núcleo de listados -------
async def _compute_lists(session: AsyncSession):
    # Profes (para mapa de nombres y ordenación)
    teachers = (await session.execute(select(Teacher))).scalars().all()
    t_by_id: Dict[int, Teacher] = {int(t.id): t for t in teachers}

    # Si no tenemos modelo de sustituciones aún → todo el mundo es “inicial” y “actual”, no hay “sustituidos”
    if Substitution is None:
        names = sorted([t.name for t in teachers], key=_sort_key)  # <-- sin tildes
        return names, names, []

    # Con modelo de sustituciones
    subs = (await session.execute(select(Substitution))).scalars().all()

    all_sub_ids = set()
    active_pairs: List[Tuple[int, int]] = []
    for s in subs:
        sub_id, rep_id, active = _extract_ids_and_is_active(s)
        if sub_id:
            all_sub_ids.add(sub_id)
        if active and sub_id and rep_id:
            active_pairs.append((sub_id, rep_id))

    # INICIAL: no sustitutos (nunca han sido substitute_id)
    initial_ids = [tid for tid in t_by_id.keys() if tid not in all_sub_ids]
    initial_names = sorted([t_by_id[tid].name for tid in initial_ids], key=_sort_key)  # <-- sin tildes

    # ACTUAL (hoy)
    active_sub_to_rep = {sub: rep for sub, rep in active_pairs}
    active_rep_to_sub = {rep: sub for sub, rep in active_pairs}
    active_sub_ids = set(active_sub_to_rep.keys())
    active_rep_ids = set(active_rep_to_sub.keys())

    current_display: List[str] = []
    for tid, t in t_by_id.items():
        if tid in active_rep_ids:
            continue  # sustituido hoy → no lo listamos como “titular”
        if tid in active_sub_ids:
            rep_id = active_sub_to_rep.get(tid)
            rep_name = t_by_id.get(rep_id).name if rep_id in t_by_id else "—"
            current_display.append(f"{t.name} ({rep_name})")
        else:
            current_display.append(t.name)
    current_display = sorted(current_display, key=_sort_key)  # <-- sin tildes

    # SUSTITUIDOS (segunda lista en “Actual”), ordenados por nombre del sustituido
    replaced_display: List[str] = []
    for rep_id in sorted(
        list(active_rep_ids),
        key=lambda rid: _sort_key(t_by_id[rid].name) if rid in t_by_id else ""
    ):
        rep_name = t_by_id.get(rep_id).name if rep_id in t_by_id else "—"
        sub_id = active_rep_to_sub.get(rep_id)
        sub_name = t_by_id.get(sub_id).name if sub_id in t_by_id else "—"
        replaced_display.append(f"{rep_name} ({sub_name})")

    return initial_names, current_display, replaced_display


# ------- rutas -------
@router.get("/teachers/list")
async def teachers_list(request: Request, session: AsyncSession = Depends(get_session)):
    initial, current, replaced = await _compute_lists(session)
    return _templates(request).TemplateResponse(
        "teachers_list.html",
        _ctx(request, initial_list=initial, current_list=current, replaced_list=replaced),
    )


@router.get("/teachers/list/pdf")
async def teachers_list_pdf(
    view: str = Query(..., pattern="^(initial|current|replaced)$"),
    session: AsyncSession = Depends(get_session),
):
    initial, current, replaced = await _compute_lists(session)
    title_map = {
        "initial": "Profesorado Inicial (no sustitutos)",
        "current": "Profesorado Actual",
        "replaced": "Profesores sustituidos (hoy)",
    }
    data_map = {"initial": initial, "current": current, "replaced": replaced}

    # Si quieres reforzar el orden sin tildes también en el PDF:
    items = sorted(data_map[view], key=_sort_key)

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    generate_teachers_list_pdf(
        path=tmp.name,
        center_name=(settings.INSTITUTION_NAME or ""),
        title=title_map[view],
        items=items,
    )
    return FileResponse(tmp.name, media_type="application/pdf", filename=f"{view}_profesorado.pdf")
