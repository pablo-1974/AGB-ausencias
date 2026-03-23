from fastapi import APIRouter, Depends, Request, UploadFile, Form
from fastapi.responses import RedirectResponse, FileResponse
from auth import admin_required
from database import get_session, AsyncSessionLocal
from models import User
from context import ctx
from services.backup import (
    export_backup,
    clear_tables,
    load_backup_from_excel,
    VALID_TABLES
)

import tempfile
import os

router = APIRouter()

# ---------------------------------------------
# Igual que en absences_router
# ---------------------------------------------
def _templates(request: Request):
    return request.app.state.templates


# =============================================
# PANTALLA PRINCIPAL
# =============================================
@router.get("/admin/backup")
async def admin_backup(request: Request, admin: User = Depends(admin_required)):
    user = admin
    return _templates(request).TemplateResponse(
        "admin_backup.html",
        ctx(request, user)
    )


# =============================================
# DESCARGAR COPIA
# =============================================
@router.get("/admin/backup/download")
async def admin_backup_download(request: Request, admin: User = Depends(admin_required)):
    user = admin
    async with AsyncSessionLocal() as db:
        path = await export_backup(db, user)

    filename = os.path.basename(path)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# =============================================
# PANTALLA DE SELECCIÓN PARA VACIAR TABLAS
# =============================================
@router.get("/admin/backup/clear")
async def admin_backup_clear(request: Request, admin: User = Depends(admin_required)):
    user = admin
    return _templates(request).TemplateResponse(
        "admin_backup_clear.html",
        ctx(request, user, valid_tables=VALID_TABLES)
    )


# =============================================
# EJECUCIÓN DE VACIADO
# =============================================
@router.post("/admin/backup/clear")
async def admin_backup_clear_post(
    request: Request,
    admin: User = Depends(admin_required),
    tables: list[str] = Form(default=[])
):
    user = admin

    async with AsyncSessionLocal() as db:
        # Descargar antes de borrar
        await export_backup(db, user)
        await clear_tables(db, tables)

    return RedirectResponse("/admin/backup", 303)


# =============================================
# PANTALLA PARA CARGAR COPIA
# =============================================
@router.get("/admin/backup/load")
async def admin_backup_load_page(request: Request, admin: User = Depends(admin_required)):
    user = admin
    return _templates(request).TemplateResponse(
        "admin_backup_load.html",
        ctx(request, user)
    )


# =============================================
# PROCESAR CARGA DE COPIA
# =============================================
@router.post("/admin/backup/load")
async def admin_backup_load(
    request: Request,
    admin: User = Depends(admin_required),
    backup_file: UploadFile = None
):
    user = admin

    if not backup_file:
        return RedirectResponse("/admin/backup", 303)

    # Guardar temporal
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(await backup_file.read())
        temp_path = tmp.name

    async with AsyncSessionLocal() as db:
        await load_backup_from_excel(db, temp_path)

    return RedirectResponse("/admin/backup", 303)
