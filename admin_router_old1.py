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

from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from auth import admin_required
from models import User
from config import settings

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
