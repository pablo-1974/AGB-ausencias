# ======================================================
# admin_router.py — PANEL DE ADMINISTRACIÓN
# ======================================================
# Panel central donde se organizan accesos a:
#   - Edición de profesores
#   - Edición de ausencias
#   - Edición de bajas
#   - Importaciones
#   - Calendario y herramientas avanzadas
#
# Ahora usa SOLO el contexto global ctx() para que todas
# las pantallas tengan fecha/hora, logo y variables unificadas.
# ======================================================

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy import and_

from database import get_session
from auth import admin_required
from models import User, ActionLog
from config import settings

from typing import Optional
from datetime import date

# 🔥 Contexto global unificado
from context import ctx

router = APIRouter(prefix="/admin", tags=["admin"])


# ======================================================
# Helpers de plantillas Jinja2
# ======================================================
def _templates(request: Request):
    """Devuelve el motor de plantillas cargado en app.state."""
    return request.app.state.templates


# ======================================================
# /admin/panel  — Panel de control del administrador
# ======================================================

@router.get("/panel")
async def admin_panel(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),
):
    """
    Carga el panel principal de administración.
    En versiones futuras aquí se podrán añadir KPIs,
    estadísticas de centro, avisos, etc.
    """
    return _templates(request).TemplateResponse(
        "admin_panel.html",
        ctx(request, admin, title="Panel del Administrador"),
    )

# ======================================================
# /admin/actions — Registro de acciones del sistema
# ======================================================

@router.get("/actions")
async def admin_actions(
    request: Request,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(admin_required),

    # ➕ Filtros
    user_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    entity: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    text: Optional[str] = Query(None),
):
    query = select(ActionLog)
    conditions = []

    # Aplicar filtros dinámicos
    if user_id:
        conditions.append(ActionLog.user_id == user_id)

    if action:
        conditions.append(ActionLog.action == action)

    if entity:
        conditions.append(ActionLog.entity == entity)

    if date_from:
        conditions.append(ActionLog.created_at >= date_from)

    if date_to:
        conditions.append(ActionLog.created_at <= date_to)

    if text:
        conditions.append(ActionLog.detail.ilike(f"%{text}%"))

    if conditions:
        query = query.where(and_(*conditions))

    logs = (
        await session.execute(
            query.order_by(ActionLog.created_at.desc()).limit(200)
        )
    ).scalars().all()

    # Usuarios para el selector
    users = (
        await session.execute(
            select(User).order_by(User.name.asc())
        )
    ).scalars().all()

    return _templates(request).TemplateResponse(
        "admin_actions.html",
        ctx(
            request,
            admin,
            title="Registro de acciones",
            logs=logs,
            users=users,
            filters={
                "user_id": user_id,
                "action": action,
                "entity": entity,
                "date_from": date_from,
                "date_to": date_to,
                "text": text,
            },
        ),
    )
