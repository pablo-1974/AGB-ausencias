from fastapi import APIRouter, Depends, Request, UploadFile, Form
from fastapi.responses import RedirectResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from database import AsyncSessionLocal
from auth import admin_required
from utils import templates, _ctx
from services.backup import (
    export_backup,
    clear_tables,
    load_backup_from_excel,
    VALID_TABLES
)
import tempfile
import os

router = APIRouter()


# ---------------------------------------------------------
# PANTALLA PRINCIPAL
# ---------------------------------------------------------
@router.get("/admin/backup")
async def admin_backup(request: Request, admin=Depends(admin_required)):
    return templates.TemplateResponse(
        "admin_backup.html",
        _ctx(request, user=admin)
    )


# ---------------------------------------------------------
# DESCARGAR COPIA
# ---------------------------------------------------------
@router.get("/admin/backup/download")
async def admin_backup_download(request: Request, admin=Depends(admin_required)):
    async with AsyncSessionLocal() as db:
        path = await export_backup(db, admin)

    filename = os.path.basename(path)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ---------------------------------------------------------
# PANTALLA DE SELECCIÓN DE TABLAS A VACIAR
# ---------------------------------------------------------
@router.get("/admin/backup/clear")
async def admin_backup_clear(request: Request, admin=Depends(admin_required)):
    return templates.TemplateResponse(
        "admin_backup_clear.html",
        _ctx(request, user=admin, valid_tables=VALID_TABLES)
    )


# ---------------------------------------------------------
# EJECUTAR VACIADO (POST)
# ---------------------------------------------------------
@router.post("/admin/backup/clear")
async def admin_backup_clear_post(
    request: Request,
    admin=Depends(admin_required),
    tables: list[str] = Form(default=[])
):
    async with AsyncSessionLocal() as db:
        # Antes de borrar → descargar copia
        backup_path = await export_backup(db, admin)
        # Ahora borrar solamente las tablas marcadas
        await clear_tables(db, tables)

    # Redirigir con mensaje
    response = RedirectResponse("/admin/backup", status_code=303)
    response.set_cookie("msg", "Tablas vaciadas correctamente.")
    return response


# ---------------------------------------------------------
# CARGAR DESDE COPIA DE SEGURIDAD
# ---------------------------------------------------------
@router.post("/admin/backup/load")
async def admin_backup_load(
    request: Request,
    admin=Depends(admin_required),
    backup_file: UploadFile = None
):
    if not backup_file:
        return RedirectResponse("/admin/backup", status_code=303)

    # Guardar temporalmente
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(await backup_file.read())
        temp_path = tmp.name

    async with AsyncSessionLocal() as db:
        await load_backup_from_excel(db, temp_path)

    return RedirectResponse("/admin/backup", status_code=303)
